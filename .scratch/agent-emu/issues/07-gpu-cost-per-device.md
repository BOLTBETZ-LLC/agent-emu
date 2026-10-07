# GPU cost per Device

Type: research
Status: open
Blocked by:

## Question

What does host-GPU rendering cost per Device when 10 Devices share one NVIDIA RTX 5060 Ti on Windows? Answer these:

- How much VRAM and host RAM does gfxstream or a virtio-gpu backend use per guest?
- What does a 1080x2400 surface plus swapchain cost?
- Known Windows/NVIDIA problems, such as gfxstream issue #198 (write-combined memory slowness under WHPX).
- Do any guest graphics buffers count against host RAM, and so against the 400 MB?
- How does it compare with software rendering (lavapipe, SwiftShader) per Device?
