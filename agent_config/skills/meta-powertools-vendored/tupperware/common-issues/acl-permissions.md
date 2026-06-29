# ACL & Permissions

> 109 posts in TW Group FAQ | Primary Scuba: `hipster_aclchecker_checks` | Primary CLI: `tw ssh`

## Debugging Playbook

### Step 1: Identify the exact error message
**CLI**: `tw ssh <task_handle>` or retry the failing command. Look for: "Permission denied", "Unauthorized (delegated identities)", "ACL failed on action(s)", "CREDENTIAL_FETCHING_FAILURE". Note: `tw ssh` may also be blocked by the BPF jailer on certain hosts, not just ACL issues.

| Error pattern | Go to |
|--------------|-------|
| "Unauthorized (delegated identities)" with ACL name | [ACL Rejection Details](#acl-rejection-details) |
| `tw ssh` "Permission denied" | [SSH Permission Denied](#ssh-permission-denied) |
| Job stuck in "Enabling/Disabling SMC" | [SMC Stuck States](#jobs-stuck-in-enablingdisabling-smc) |
| `tw restart` succeeds but nothing happens | [Silent ACL Failures](#silent-acl-failures) |

---

### ACL Rejection Details
**Scuba**: `hipster_aclchecker_checks`
- Columns: `deciding_accessor`, `deciding_action`, `deciding_resource_name`, `deciding_result`, `time`
- Filter: `deciding_resource_name = <ACL_name_from_error>`
- Look at which identity was denied and which action failed (e.g., `modifyEndpoints`, `modifyTierProperties`, `modifyHierarchy`, `delete`)
**CLI**: `bunnylol hipster <ACL_name>` to navigate to the ACL permissions page
- Fix: Add the job's service identity to the ACL for the required action
- If the denied identity is `tupperware.schedulers`, the scheduler also needs permission on the ACL
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3533482150291652), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3561574794149054)

### SSH Permission Denied
**CLI**: `tw ssh root <task_handle>` (use `root` to enter the cgroup properly)
- If this also fails, you need `tupperware` permission on the relevant ACL
**Scuba**: `hipster_aclchecker_checks`
- Filter: `deciding_action = tupperware`, `deciding_accessor = <your_identity>`
- Check: TIER ACL, DATA_PROJECT ACL, or CAPACITY_RESERVATION ACL
**CLI**: `bunnylol hipster TIER:<tier_name>` to request the `tupperware` or `tupperware_log` action
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3621578858148647), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3495727860733748)

### Jobs Stuck in Enabling/Disabling SMC
**Scuba**: `smcbridge_v2_errors`
- Columns: `job_handle`, `error_msg`, `tier_name`, `error_code`
- Filter: `job_handle = <your_job_handle>`
- Common cause: the job's service identity is missing from the SMC tier ACL
- Fix: Add the service identity (found in TW UI > Advanced > Security) to the SMC tier ACL for `modifyEndpoints`, `modifyTierProperties`, and `modifyHierarchy` actions
- Also add `tupperware.schedulers` if missing
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3594226174217249), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3566887560284444)

### Silent ACL Failures
**Scuba**: `tupperware_api_service_cpp`
- Columns: `job_handle`, `method_name`, `error_type`
- Filter: `job_handle = <your_job_handle>`, look for async validation errors
- The TW API validation is currently async, so CLI may not reject at request time
- Common cause: service identity lacks `tupperware_machine_admin` permission on `MACHINE_TIER` ACL
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3493333127639888)

### Verify Fix
**CLI**: `tw ssh <task_handle>` or retry the original failing command
- If SMC-related, wait a few minutes for the scheduler to retry the SMC sync

## Best Practices & How-To

### How to set up SMC ACLs correctly for a new TW job
Three identities need permissions on the SMC tier ACL: (1) the job's own service identity, (2) `tupperware.schedulers`, and (3) `shardmanager` (if using ShardManager). Grant `modifyEndpoints`, `modifyTierProperties`, `modifyHierarchy`, and `delete` actions. If using a parent tier, grant permissions on the parent tier ACL as well.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3597596040546929), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3680441248929074)

### How to configure service identities for prod vs test jobs
Use separate service identities for different environments. The identity framework supports multiple identities per job. Configure different ACL groups for prod vs katchin clusters. To have different ACLs, update the job with a different service identity.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/2697204850586057), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3546503198989547)

### How to transfer ACLs when changing job ownership
When moving jobs between oncall teams, transfer all relevant permissions: SMC tier ACL, MACHINE_TIER ACL, CAPACITY_RESERVATION ACL, and TIER ACL. Missing permissions on any of these can cause silent failures. Check auto-renew settings -- some grants expire silently.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3494021104237757), [2](https://fb.workplace.com/groups/1473492212957333/permalink/3566887560284444)

### How to apply Linux capability changes (e.g., SYS_NICE)
A `tw restart` is insufficient for capability changes to take effect. A full `tw job update` is required. After updating, verify the capability is active inside the container.
**Example posts**: [1](https://fb.workplace.com/groups/1473492212957333/permalink/3494945577478643)

## Common Questions

### Q: Can multiple service identities be used on a single TW job?
**A:** Yes. Multiple identities can be configured. By default, all identities are sent with requests. ACL logic (OR vs AND) depends on the service being accessed -- usually if any supplied identity has access, the request succeeds.

### Q: How do I get tw ssh access to my task?
**A:** Someone needs to add you to the `tupperware` permission on the relevant ACL (TIER, DATA_PROJECT, or CAPACITY_RESERVATION). Use `bunnylol hipster <ACL_type>:<ACL_name>` to navigate to the permissions page.

### Q: How is SSH access to TW hosts determined?
**A:** SSH access checks the requestor's access to all jobs on the host and the capacity reservation. SAH_NODE ACL controls root SSH access. CAPACITY_RESERVATION ACL only controls starting TW jobs, not SSH. TIER ACL only applies when a task is actively allocated.

## Reference Tables

### Scuba Tables
| Table | When to Use | Key Columns |
|-------|------------|-------------|
| `hipster_aclchecker_checks` | Debug any ACL check result | `deciding_accessor`, `deciding_action`, `deciding_resource_name`, `deciding_result` |
| `smcbridge_v2_errors` | Debug SMC tier ACL issues | `job_handle`, `error_msg`, `tier_name`, `error_code` |
| `tupperware_api_service_cpp` | Find silent/async ACL failures | `job_handle`, `method_name`, `error_type` |

### CLI Commands
| Command | When to Use |
|---------|------------|
| `tw ssh <task_handle>` | SSH into a task (requires ACL) |
| `tw ssh root <task_handle>` | SSH as root with proper cgroup placement |
| `bunnylol hipster <ACL_type>:<ACL_name>` | Navigate to ACL permissions page |
| `hipstercli add --reason '...' <ACL> <action> <identity> --diff` | Grant ACL permission via diff |
| `tw job update <job_handle>` | Apply capability changes (not just restart) |
| `tw print <job_handle>` | Check job's service identity and ACL config |
