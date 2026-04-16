// Patch globalThis.fetch and WebSocket to route through fwdproxy via HTTP CONNECT tunnel
const http = require('node:http');
const https = require('node:https');
const tls = require('node:tls');
const { URL } = require('node:url');

const PROXY_HOST = 'fwdproxy';
const PROXY_PORT = 8080;

function createTunnel(targetHost, targetPort) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: PROXY_HOST,
      port: PROXY_PORT,
      method: 'CONNECT',
      path: `${targetHost}:${targetPort}`,
    });
    req.on('connect', (res, socket) => {
      if (res.statusCode === 200) {
        resolve(socket);
      } else {
        socket.destroy();
        reject(new Error(`Proxy CONNECT failed: ${res.statusCode}`));
      }
    });
    req.on('error', reject);
    req.end();
  });
}

const originalFetch = globalThis.fetch;

globalThis.fetch = async function patchedFetch(input, init) {
  const url = typeof input === 'string' ? new URL(input) : new URL(input.url);

  if (url.protocol !== 'https:') {
    return originalFetch(input, init);
  }

  const targetHost = url.hostname;
  const targetPort = parseInt(url.port || '443', 10);

  const tunnelSocket = await createTunnel(targetHost, targetPort);

  const tlsSocket = tls.connect({
    socket: tunnelSocket,
    servername: targetHost,
    rejectUnauthorized: true,
  });

  await new Promise((resolve, reject) => {
    tlsSocket.on('secureConnect', resolve);
    tlsSocket.on('error', reject);
  });

  return new Promise((resolve, reject) => {
    const method = (init && init.method) || 'GET';
    const bodyData = init && init.body
      ? (typeof init.body === 'string' ? init.body : String(init.body))
      : null;

    const headers = {};
    if (init && init.headers) {
      if (init.headers instanceof Headers) {
        init.headers.forEach((v, k) => { headers[k] = v; });
      } else if (typeof init.headers === 'object') {
        Object.assign(headers, init.headers);
      }
    }
    headers['Host'] = targetHost;
    if (bodyData) {
      headers['Content-Length'] = Buffer.byteLength(bodyData);
    }

    const reqOpts = {
      hostname: targetHost,
      port: targetPort,
      path: url.pathname + url.search,
      method: method,
      headers: headers,
      socket: tlsSocket,
      createConnection: () => tlsSocket,
    };

    const req = https.request(reqOpts, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => {
        const body = Buffer.concat(chunks);
        const responseHeaders = {};
        for (const [key, val] of Object.entries(res.headers)) {
          if (val !== undefined) responseHeaders[key] = Array.isArray(val) ? val.join(', ') : val;
        }
        const response = new Response(body, {
          status: res.statusCode,
          statusText: res.statusMessage,
          headers: responseHeaders,
        });
        resolve(response);
        tlsSocket.destroy();
      });
    });

    req.on('error', (err) => {
      tlsSocket.destroy();
      reject(err);
    });

    if (bodyData) {
      req.write(bodyData);
    }
    req.end();
  });
};

// Patch WebSocket: replace native WebSocket with the `ws` library configured
// to tunnel through fwdproxy via HTTP CONNECT.
const WsWebSocket = require('ws');

class ProxiedWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  constructor(url, protocols) {
    this.url = url;
    this.readyState = ProxiedWebSocket.CONNECTING;
    this.binaryType = 'arraybuffer';
    this.onopen = null;
    this.onclose = null;
    this.onmessage = null;
    this.onerror = null;
    this._ws = null;
    this._connect(url, protocols);
  }

  async _connect(url, protocols) {
    try {
      const parsed = new URL(url);
      const targetHost = parsed.hostname;
      const targetPort = parseInt(parsed.port || '443', 10);

      const tunnelSocket = await createTunnel(targetHost, targetPort);

      const tlsSocket = tls.connect({
        socket: tunnelSocket,
        servername: targetHost,
        rejectUnauthorized: true,
      });

      await new Promise((resolve, reject) => {
        tlsSocket.on('secureConnect', resolve);
        tlsSocket.on('error', reject);
      });

      const wsOpts = {
        createConnection: () => tlsSocket,
      };
      if (protocols) {
        wsOpts.protocols = Array.isArray(protocols) ? protocols : [protocols];
      }

      const ws = new WsWebSocket(url, wsOpts);
      this._ws = ws;

      ws.on('open', () => {
        this.readyState = ProxiedWebSocket.OPEN;
        const ev = { type: 'open' };
        if (this.onopen) this.onopen(ev);
      });

      ws.on('message', (data, isBinary) => {
        let payload;
        if (this.binaryType === 'arraybuffer' && isBinary) {
          payload = data instanceof ArrayBuffer ? data : data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
        } else if (!isBinary) {
          payload = data.toString();
        } else {
          payload = data instanceof ArrayBuffer ? data : data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
        }
        const ev = { type: 'message', data: payload };
        if (this.onmessage) this.onmessage(ev);
      });

      ws.on('close', (code, reason) => {
        this.readyState = ProxiedWebSocket.CLOSED;
        const ev = { type: 'close', code: code || 1006, reason: reason ? reason.toString() : '', wasClean: code === 1000 };
        if (this.onclose) this.onclose(ev);
      });

      ws.on('error', (err) => {
        const ev = { type: 'error', message: err.message, error: err };
        if (this.onerror) this.onerror(ev);
      });
    } catch (err) {
      this.readyState = ProxiedWebSocket.CLOSED;
      const ev = { type: 'error', message: err.message, error: err };
      if (this.onerror) this.onerror(ev);
      const closeEv = { type: 'close', code: 1006, reason: '', wasClean: false };
      if (this.onclose) this.onclose(closeEv);
    }
  }

  send(data) {
    if (this._ws && this._ws.readyState === WsWebSocket.OPEN) {
      this._ws.send(data);
    }
  }

  close(code, reason) {
    this.readyState = ProxiedWebSocket.CLOSING;
    if (this._ws) {
      this._ws.close(code, reason);
    }
  }

  get CONNECTING() { return 0; }
  get OPEN() { return 1; }
  get CLOSING() { return 2; }
  get CLOSED() { return 3; }
}

globalThis.WebSocket = ProxiedWebSocket;
