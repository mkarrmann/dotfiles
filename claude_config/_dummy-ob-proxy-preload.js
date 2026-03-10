// Route all network traffic through fwdproxy on Meta devservers.
//
// Strategy:
// 1. Patch https.Agent.createConnection — async-friendly, uses http.request
//    CONNECT to establish tunnel cleanly, then wraps in TLS. Covers https.get,
//    https.request, and anything using the default HTTPS agent.
// 2. Patch tls.connect — for anything calling tls.connect directly.
// 3. Patch net.connect — for non-TLS external TCP connections.
// 4. Patch globalThis.fetch — manual tunnel + TLS for HTTPS fetch requests.
// 5. Patch net.Socket.prototype.connect — lowest-level catch-all for code that
//    captured references to tls.connect/net.connect at startup (e.g. Node's
//    bundled undici used by WebSocket/fetch).
const http = require('node:http');
const https = require('node:https');
const tls = require('node:tls');
const net = require('node:net');
const { URL } = require('node:url');

const PROXY_HOST = 'fwdproxy';
const PROXY_PORT = 8080;

const DBG = process.env.PROXY_DEBUG
  ? (msg) => process.stderr.write(`[proxy] ${msg}\n`)
  : () => {};

DBG('proxy-preload loaded (pid=' + process.pid + ')');

function isLocalHost(host) {
  if (!host) return true;
  return host === 'localhost' ||
    host === '127.0.0.1' ||
    host === '::1' ||
    host.endsWith('.facebook.com') ||
    host.endsWith('.tfbnw.net') ||
    host.endsWith('.fb.com') ||
    host === PROXY_HOST;
}

function normalizeConnectArgs(args) {
  if (typeof args[0] === 'object' && args[0] !== null && !Array.isArray(args[0])) {
    return args[0];
  }
  const opts = {};
  if (typeof args[0] === 'number') {
    opts.port = args[0];
    if (typeof args[1] === 'string') {
      opts.host = args[1];
    }
  }
  return opts;
}

// Helper: establish a CONNECT tunnel via http.request (clean, handles protocol)
function connectTunnel(host, port) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: PROXY_HOST,
      port: PROXY_PORT,
      method: 'CONNECT',
      path: `${host}:${port}`,
    });
    req.on('connect', (res, socket, head) => {
      if (res.statusCode === 200) {
        if (head && head.length) socket.unshift(head);
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

// ── 1. Patch https.Agent.createConnection ─────────────────
//
// The HTTPS Agent's createConnection accepts an async callback (oncreate),
// so we can establish the CONNECT tunnel fully before creating the TLSSocket.
// This is the cleanest approach — no stream hacks needed.

const originalTlsConnect = tls.connect;
const originalCreateConnection = https.Agent.prototype.createConnection;

https.Agent.prototype.createConnection = function (options, oncreate) {
  const host = options.host || options.servername || 'localhost';
  const port = options.port || 443;

  if (isLocalHost(host)) {
    return originalCreateConnection.call(this, options, oncreate);
  }

  DBG(`Agent.createConnection → ${host}:${port} (tunneling)`);

  const proxyReq = http.request({
    host: PROXY_HOST,
    port: PROXY_PORT,
    method: 'CONNECT',
    path: `${host}:${port}`,
  });

  proxyReq.on('connect', (res, socket, head) => {
    if (res.statusCode === 200) {
      DBG(`CONNECT ${host}:${port} → 200, starting TLS`);
      if (head && head.length) socket.unshift(head);

      const tlsSocket = originalTlsConnect.call(tls, {
        socket: socket,
        servername: options.servername || host,
        rejectUnauthorized: options.rejectUnauthorized !== false,
      });

      tlsSocket.on('error', (err) => DBG(`TLS error: ${err.message}`));

      if (oncreate) oncreate(null, tlsSocket);
    } else {
      DBG(`CONNECT ${host}:${port} → ${res.statusCode}`);
      const err = new Error(`Proxy CONNECT failed: ${res.statusCode}`);
      if (oncreate) oncreate(err);
    }
  });

  proxyReq.on('error', (err) => {
    DBG(`CONNECT error: ${err.message}`);
    if (oncreate) oncreate(err);
  });

  proxyReq.end();
  // Return undefined — oncreate will be called asynchronously
};

// ── 2. Patch tls.connect ──────────────────────────────────
//
// For code that calls tls.connect directly (e.g. undici WebSocket internals),
// we use http.request CONNECT + handle transfer. The tunnel socket from
// http.request has its _handle transferred to a placeholder that TLSSocket wraps.

tls.connect = function (...args) {
  const opts = normalizeConnectArgs(args);
  const cb = typeof args[args.length - 1] === 'function' ? args[args.length - 1] : undefined;
  const host = opts.host || opts.servername || 'localhost';
  const port = opts.port || 443;

  if (isLocalHost(host) || opts.socket) {
    return originalTlsConnect.apply(tls, args);
  }

  DBG(`tls.connect → ${host}:${port} (tunneling via handle transfer)`);

  // Create a placeholder socket for TLSSocket to wrap.
  // It has no _handle yet, so TLSSocket will wait for 'connect'.
  const placeholder = new net.Socket();

  const tlsOpts = Object.assign({}, opts, {
    socket: placeholder,
    servername: opts.servername || host,
  });
  delete tlsOpts.host;
  delete tlsOpts.port;

  const tlsSocket = cb
    ? originalTlsConnect.call(tls, tlsOpts, cb)
    : originalTlsConnect.call(tls, tlsOpts);

  // Establish CONNECT tunnel asynchronously
  const proxyReq = http.request({
    host: PROXY_HOST,
    port: PROXY_PORT,
    method: 'CONNECT',
    path: `${host}:${port}`,
  });

  proxyReq.on('connect', (res, socket, head) => {
    if (res.statusCode === 200) {
      DBG(`tls.connect CONNECT ${host}:${port} → 200, transferring handle`);
      if (head && head.length) socket.unshift(head);

      // Transfer the tunnel socket's handle to our placeholder.
      // TLSSocket is waiting for 'connect' on placeholder to call
      // ssl.receive(placeholder._handle).
      const handle = socket._handle;
      if (handle) {
        // Update the handle's owner reference to point to placeholder
        for (const sym of Object.getOwnPropertySymbols(handle)) {
          if (handle[sym] === socket) {
            handle[sym] = placeholder;
            break;
          }
        }
        placeholder._handle = handle;
        socket._handle = null;
        placeholder.readable = true;
        placeholder.writable = true;
        placeholder.connecting = false;

        // Now emit 'connect' — TLSSocket receives the handle and starts TLS
        placeholder.emit('connect');
      }
    } else {
      tlsSocket.destroy(new Error(`Proxy CONNECT failed: ${res.statusCode}`));
    }
  });

  proxyReq.on('error', (err) => {
    tlsSocket.destroy(err);
  });

  proxyReq.end();
  return tlsSocket;
};

// ── 3. Patch net.connect for non-TLS external connections ──

const originalNetConnect = net.connect;

net.connect = net.createConnection = function (...args) {
  const opts = normalizeConnectArgs(args);
  const host = opts.host || 'localhost';
  const port = opts.port || 443;

  if (isLocalHost(host)) {
    return originalNetConnect.apply(net, args);
  }

  DBG(`net.connect → ${host}:${port} (tunneling)`);

  const socket = new net.Socket();

  const proxyReq = http.request({
    host: PROXY_HOST,
    port: PROXY_PORT,
    method: 'CONNECT',
    path: `${host}:${port}`,
  });

  proxyReq.on('connect', (res, tunnelSocket) => {
    if (res.statusCode === 200) {
      tunnelSocket.pipe(socket);
      socket.pipe(tunnelSocket);
      socket.emit('connect');
      tunnelSocket.on('error', (err) => socket.destroy(err));
      tunnelSocket.on('close', () => socket.destroy());
      socket.on('close', () => tunnelSocket.destroy());
    } else {
      socket.destroy(new Error(`Proxy CONNECT failed: ${res.statusCode}`));
    }
  });

  proxyReq.on('error', (err) => {
    socket.destroy(err);
  });

  proxyReq.end();
  return socket;
};

// ── 4. Patch fetch ────────────────────────────────────────

const originalFetch = globalThis.fetch;

globalThis.fetch = async function patchedFetch(input, init) {
  const url = typeof input === 'string' ? new URL(input) : new URL(input.url);

  if (url.protocol !== 'https:') {
    return originalFetch(input, init);
  }

  const targetHost = url.hostname;
  const targetPort = parseInt(url.port || '443', 10);

  DBG(`fetch → ${targetHost} (tunneling)`);

  const tunnelSocket = await connectTunnel(targetHost, targetPort);

  const tlsSocket = originalTlsConnect.call(tls, {
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

// ── 5. Patch net.Socket.prototype.connect ─────────────────
//
// Lowest-level catch-all. Node's bundled undici (WebSocket, fetch) may capture
// references to tls.connect/net.connect at startup, bypassing module-level
// patches. But every TCP connection ultimately calls socket.connect() on the
// prototype, which we can intercept here.

const origSocketConnect = net.Socket.prototype.connect;

net.Socket.prototype.connect = function (...args) {
  const opts = normalizeConnectArgs(args);
  const host = opts.host || opts.hostname || 'localhost';
  const port = opts.port;

  if (!port || opts.path || isLocalHost(host)) {
    return origSocketConnect.apply(this, args);
  }

  DBG(`Socket.connect → ${host}:${port} (tunneling)`);

  const self = this;
  const cb = typeof args[args.length - 1] === 'function' ? args[args.length - 1] : undefined;

  if (cb) {
    this.once('connect', cb);
  }

  this.connecting = true;

  const proxyReq = http.request({
    host: PROXY_HOST,
    port: PROXY_PORT,
    method: 'CONNECT',
    path: `${host}:${port}`,
  });

  proxyReq.on('connect', (res, tunnelSocket, head) => {
    if (res.statusCode === 200) {
      DBG(`Socket.connect tunnel ${host}:${port} → 200, transferring handle`);
      if (head && head.length) tunnelSocket.unshift(head);

      const handle = tunnelSocket._handle;
      if (handle) {
        for (const sym of Object.getOwnPropertySymbols(handle)) {
          if (handle[sym] === tunnelSocket) {
            handle[sym] = self;
            break;
          }
        }
        self._handle = handle;
        tunnelSocket._handle = null;
        self.readable = true;
        self.writable = true;
        self.connecting = false;

        self.emit('connect');
      } else {
        self.destroy(new Error('Tunnel socket has no handle'));
      }
    } else {
      self.destroy(new Error(`Proxy CONNECT failed: ${res.statusCode}`));
    }
  });

  proxyReq.on('error', (err) => {
    self.destroy(err);
  });

  proxyReq.end();
  return this;
};
