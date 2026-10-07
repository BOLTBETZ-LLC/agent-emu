# Glossary

**Device**
One running virtual Android phone that an agent tests an app on.
_Avoid_: instance, emulator (that word means the whole product), AVD.

**Fleet**
All the Devices running at once on one host.

**Host**
The physical computer that runs the Fleet.

**Unique memory**
Host RAM that one Device uses and no other Device shares. Memory shared across the Fleet counts once for the whole Fleet, not once per Device.
_Avoid_: guest RAM, configured memory.

**Proof app**
The specific app build used to prove the emulator works. It is fixed to one build so results are comparable.

**First screen**
The first screen an app shows after launch, with no user logged in.
_Avoid_: home screen (ambiguous with the logged-in home).

**Computer-use control**
An agent driving a Device the way a person would: looking at the screen and tapping, typing and swiping.

**Agent API**
The structured way an agent reads and changes a Device's state directly (UI tree, app state, network, logs), as opposed to computer-use control.

**Round trip**
The time from an agent asking for a screenshot or sending an input to the result being in the agent's hands.

**Fork**
Making a new Device from the exact current state of a running Device.
