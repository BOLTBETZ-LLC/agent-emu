# Memory budget

Type: grilling
Status: open
Blocked by: 03, 05, 07, 08

## Question

Split the 400 MB unique-memory budget per Device across its parts: VMM process, kernel, native daemons, zygote and boot image (shared or private), system_server, graphics buffers, and the proof app. Decide which cuts the spec commits to. Decide what the plan is if the measured app alone leaves too little room.
