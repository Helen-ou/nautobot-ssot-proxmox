# Nautobot SSoT: Proxmox (minimal)

Shamelessly stolen from user @fadenb

## Prerequisites

- Nautobot 3.x up and running
- Nautobot SSoT app installed and enabled
- A Proxmox 8.x API token with read-only privileges (PVEAuditor is fine)
- At least Ubuntu >=22.04 host for Nautobot services

## Install

1) Activate your Nautobot virtual environment (e.g., `source /opt/nautobot/venv/bin/activate` or `source .venv/bin/activate`, `su nautobot`).

2) You could install as the original contributor said with `pip install -e .`, but my configuration was messed (probably an error on my end) so I have no idea what I've done anymore.

3) Enable and do the config in `/opt/nautobot/nautobot_config.py`
```json
PLUGINS = ["nautobot_ssot", "nautobot_ssot_proxmox"]

PLUGINS_CONFIG = {
     "nautobot_ssot_proxmox": {
        "PROXMOX_URL": "192.168.252.13",
        "PROXMOX_USER": "nautobot-ssot@pve",
        "PROXMOX_TOKEN_NAME": "nautobot-proxmox",
        "PROXMOX_TOKEN_VALUE": "",
        # Your token. Remember to give it the ACLs required as it doesn't port from the user automatically.
        "VERIFY_SSL": False,
        "CLUSTER_NAME": "pve01",
        "CLUSTER_TYPE_NAME": "Proxmox VE",
        "DELETE_MISSING": False,
        "ALLOWED_SUBNETS": ["192.168.250.0/24", "192.168.216.0/24"],
    	"PARENT_PREFIX_ID": "f469ff84-5acc-4890-9a96-842228354a1d", 
        # Change the IDs of each to the corresponding ID in your Nautobot installation. Prefix was gotten manually from IPAM > Prefixes > 192.168.0.0/16 > Advanced
        # You could automate picking them up, can't be bothered anymore
        "VM_ROLE_ID": "00c588b8-0827-4803-983e-b3a5024abbc4",
        "LXC_ROLE_ID": "8cd75551-2fe8-4980-b385-41b41d48c1cf",
    },
}


```

4) Apply migrations and restart services:
```bash
    nautobot-server migrate
    nautobot-server post_upgrade
    nautobot-server collectstatic --no-input
```
```bash
    sudo systemctl restart nautobot nautobot-worker
```

## Proxmox setup

Create a dedicated API user and token in Proxmox (UI: Datacenter -> Permissions -> API Tokens). Grant the user PVEAuditor at the root path "/". Use the generated token name and secret in PLUGINS_CONFIG.

## How it works

- Job name: "Proxmox: Import inventory" under Apps -> SSoT.
- Dry-run by default. You will see the diff before applying.
- Identifiers:
  - VM: custom_fields.proxmox_vmid (immutable)
  - Cluster: name
- Synced attributes:
  - VM: name, vcpus, memory (MB), status__name ("Active" if running, else "Offline"), cluster__name, CFs proxmox_node, proxmox_type
  - IP Addresses are synced and will get them from the config of the LXCs and the qemu-guest-agent network endpoint. It won't crash if you haven't gotten them.
  TODO : Check why LXCs don't sync disk size

## Run a sync

1) UI: Apps -> SSoT -> Data Sources -> "Proxmox: Import inventory".
2) Run with dry-run first.
3) If the diff looks good, uncheck dry-run (commit) and run again.

# Example
<img width="1470" height="799" alt="example" src="https://github.com/user-attachments/assets/0b71fcb5-acaa-40ea-a138-28069e01a9f7" />


## Scheduling

Create a Scheduled Job in the Nautobot UI to run every 5 to 15 minutes, as desired.

## Extending later

- Add VMInterfaces and IPs: introduce a VMInterfaceModel and enumerate NICs via per-VM endpoints, then map to Nautobot Virtualization interfaces and IPAM.
- Multiple clusters: emit multiple ClusterModel objects and set cluster__name per VM.
- Deletion policy: keep DELETE_MISSING=False for safety; or enable and let SSoT delete.

## Troubleshooting

- Missing config keys: check PLUGINS_CONFIG in nautobot_config.py.
- SSL errors: set VERIFY_SSL=False to test, then fix CA trust.
- Status mapping: "running" -> "Active", everything else -> "Offline".
- Permissions: use a read-only Proxmox token; this job uses GET endpoints only.

## Uninstall

1) Remove "nautobot_ssot_proxmox" from PLUGINS.
2) pip uninstall nautobot-ssot-proxmox and restart Nautobot services.
