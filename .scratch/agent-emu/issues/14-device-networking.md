# Device networking

Type: grilling
Status: claimed
Blocked by:

## Question

How does each Device reach the network? Cover:

- Internet access (for the staging backend) versus isolation.
- Whether Devices can see each other or the host's localhost (Metro is out of scope, but local mock servers matter).
- Per-Device network shaping or offline mode for tests.
- The crosvm networking backend on Windows (slirp).
