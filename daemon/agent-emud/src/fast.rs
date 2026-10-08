// Fast path: input straight into virtio-input over crosvm's agent pipes, frames straight from the
// 2D GPU scanout that crosvm copies into a shared mapping. No guest shell on either path.
// crosvm side (fork, branch agent-emu-pmem): `--input multi-touch[path=\\.\pipe\..]`,
// `--input keyboard[path=\\.\pipe\..]`, env AGENT_EMU_FB=<Device dir>\fb.bin.
use crate::device::R;
use std::sync::atomic::{fence, AtomicU64, Ordering};
use std::time::Duration;
use tokio::io::AsyncWriteExt;
use tokio::net::windows::named_pipe::{ClientOptions, NamedPipeClient};
use tokio::sync::Mutex;

pub fn touch_pipe(idx: u32) -> String { format!(r"\\.\pipe\ae-touch-{idx}") }
pub fn kbd_pipe(idx: u32) -> String { format!(r"\\.\pipe\ae-kbd-{idx}") }
pub fn fb_pipe(idx: u32) -> String { format!(r"\\.\pipe\ae-fb-{idx}") }

// ---------- input ----------

const EV_SYN: u16 = 0;
const EV_KEY: u16 = 1;
const EV_ABS: u16 = 3;
const ABS_X: u16 = 0x00;
const ABS_Y: u16 = 0x01;
const ABS_MT_SLOT: u16 = 0x2f;
const ABS_MT_POSITION_X: u16 = 0x35;
const ABS_MT_POSITION_Y: u16 = 0x36;
const ABS_MT_TRACKING_ID: u16 = 0x39;
const BTN_TOUCH: u16 = 0x14a;

/// Raw 8-byte `virtio_input_event`s: le16 type, le16 code, le32 value.
pub fn ev(out: &mut Vec<u8>, ty: u16, code: u16, value: i32) {
    out.extend_from_slice(&ty.to_le_bytes());
    out.extend_from_slice(&code.to_le_bytes());
    out.extend_from_slice(&value.to_le_bytes());
}

/// MT protocol B, slot 0. `id` None lifts the finger.
pub fn touch(id: Option<i32>, x: i32, y: i32, first: bool) -> Vec<u8> {
    let mut b = Vec::with_capacity(64);
    match id {
        Some(id) => {
            ev(&mut b, EV_ABS, ABS_MT_SLOT, 0);
            if first { ev(&mut b, EV_ABS, ABS_MT_TRACKING_ID, id); }
            ev(&mut b, EV_ABS, ABS_MT_POSITION_X, x);
            ev(&mut b, EV_ABS, ABS_MT_POSITION_Y, y);
            if first { ev(&mut b, EV_KEY, BTN_TOUCH, 1); }
            ev(&mut b, EV_ABS, ABS_X, x);
            ev(&mut b, EV_ABS, ABS_Y, y);
        }
        None => {
            ev(&mut b, EV_ABS, ABS_MT_SLOT, 0);
            ev(&mut b, EV_ABS, ABS_MT_TRACKING_ID, -1);
            ev(&mut b, EV_KEY, BTN_TOUCH, 0);
        }
    }
    ev(&mut b, EV_SYN, 0, 0);
    b
}

/// Linux key codes for `key` names the virtio keyboard advertises; others fall back to the console.
pub fn linux_key(keycode: &str) -> Option<u16> {
    Some(match keycode.strip_prefix("KEYCODE_").unwrap_or(keycode) {
        "HOME" => 172, "BACK" => 158, "ENTER" => 28, "DEL" => 14, "TAB" => 15, "SPACE" => 57,
        "ESCAPE" => 1, "DPAD_UP" => 103, "DPAD_DOWN" => 108, "DPAD_LEFT" => 105, "DPAD_RIGHT" => 106,
        "MENU" => 139, "POWER" => 116, "VOLUME_UP" => 115, "VOLUME_DOWN" => 114, "MUTE" => 113,
        _ => return None,
    })
}

pub struct Input {
    touch: Mutex<NamedPipeClient>,
    kbd: Mutex<NamedPipeClient>,
    next_id: AtomicU64,
}

impl Input {
    pub fn open(idx: u32) -> R<Input> {
        let o = |n: String| ClientOptions::new().open(&n).map_err(|e| format!("input pipe {n}: {e}"));
        Ok(Input { touch: Mutex::new(o(touch_pipe(idx))?), kbd: Mutex::new(o(kbd_pipe(idx))?), next_id: AtomicU64::new(1) })
    }

    async fn send(p: &mut NamedPipeClient, b: &[u8]) -> R<()> {
        p.write_all(b).await.map_err(|e| format!("input pipe write: {e}"))
    }

    fn id(&self) -> i32 {
        (self.next_id.fetch_add(1, Ordering::Relaxed) % 10 + 1) as i32
    }

    pub async fn tap(&self, x: i32, y: i32) -> R<()> {
        let mut b = touch(Some(self.id()), x, y, true);
        b.extend(touch(None, 0, 0, false));
        Self::send(&mut *self.touch.lock().await, &b).await
    }

    /// Timed stream, one move per ~8 ms. Returns once the last event is written.
    pub async fn swipe(&self, from: (i32, i32), to: (i32, i32), ms: u64) -> R<()> {
        let mut p = self.touch.lock().await;
        let id = self.id();
        Self::send(&mut p, &touch(Some(id), from.0, from.1, true)).await?;
        let steps = (ms / 8).max(1);
        let t0 = tokio::time::Instant::now();
        for i in 1..=steps {
            tokio::time::sleep_until(t0 + Duration::from_millis(ms * i / steps)).await;
            let f = i as f64 / steps as f64;
            let at = |a: i32, b: i32| a + ((b - a) as f64 * f).round() as i32;
            Self::send(&mut p, &touch(Some(id), at(from.0, to.0), at(from.1, to.1), false)).await?;
        }
        Self::send(&mut p, &touch(None, 0, 0, false)).await
    }

    pub async fn key(&self, code: u16) -> R<()> {
        let mut b = Vec::with_capacity(32);
        ev(&mut b, EV_KEY, code, 1);
        ev(&mut b, EV_SYN, 0, 0);
        ev(&mut b, EV_KEY, code, 0);
        ev(&mut b, EV_SYN, 0, 0);
        Self::send(&mut *self.kbd.lock().await, &b).await
    }
}

// ---------- frames ----------

extern "system" {
    fn CreateFileMappingW(file: isize, sa: *const u8, protect: u32, hi: u32, lo: u32, name: *const u16) -> isize;
    fn MapViewOfFile(h: isize, access: u32, hi: u32, lo: u32, size: usize) -> *mut u8;
}
const PAGE_READONLY: u32 = 2;
const FILE_MAP_READ: u32 = 4;
const HEADER: usize = 64;

/// Read-only view of crosvm's scanout mapping (layout in crosvm `virtio_gpu.rs` `agent_fb`).
pub struct Fb {
    base: usize,
    /// crosvm re-copies the current scanout for each byte written here (None: flush-time copies only).
    refresh: Option<std::sync::Mutex<std::fs::File>>,
}

pub struct Raw {
    pub rgb: image::RgbImage,
    pub seq: u64,
    pub flush_us: u64,
}

impl Fb {
    pub fn open(path: &std::path::Path, refresh_pipe: &str) -> R<Fb> {
        use std::os::windows::fs::OpenOptionsExt;
        use std::os::windows::io::IntoRawHandle;
        let f = std::fs::OpenOptions::new().read(true).share_mode(7).open(path)
            .map_err(|e| format!("fb file {}: {e}", path.display()))?;
        // SAFETY: valid file handle; results checked. The handles and the view are never closed.
        // ponytail: leaks one file view per Device start; unmap in Device::stop if restarts get frequent.
        let base = unsafe {
            let h = CreateFileMappingW(f.into_raw_handle() as isize, std::ptr::null(), PAGE_READONLY, 0, 0, std::ptr::null());
            if h == 0 {
                return Err(format!("fb mapping {}: {}", path.display(), std::io::Error::last_os_error()));
            }
            MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0)
        };
        if base.is_null() {
            return Err(format!("fb map {}: {}", path.display(), std::io::Error::last_os_error()));
        }
        let refresh = std::fs::OpenOptions::new().read(true).write(true).open(refresh_pipe)
            .map_err(|e| eprintln!("fb refresh pipe {refresh_pipe}: {e}; flush-time frames only")).ok();
        Ok(Fb { base: base as usize, refresh: refresh.map(std::sync::Mutex::new) })
    }

    fn seq_ref(&self) -> &AtomicU64 {
        // SAFETY: the header's first 8 bytes, page-aligned.
        unsafe { &*(self.base as *const AtomicU64) }
    }

    /// Frames the guest has flushed so far (0 = none yet).
    pub fn seq(&self) -> u64 {
        // SAFETY: u64 at offset 32 of the header.
        unsafe { (*((self.base + 32) as *const AtomicU64)).load(Ordering::Acquire) }
    }

    /// Asks crosvm to re-copy the current scanout now (the flush-time copy can be a frame old).
    pub fn refresh(&self) -> R<()> {
        use std::io::{Read, Write};
        let Some(p) = &self.refresh else { return Ok(()) };
        let mut p = p.lock().unwrap();
        let mut ack = [0u8; 8];
        p.write_all(&[1]).and_then(|_| p.read_exact(&mut ack)).map_err(|e| format!("fb refresh: {e}"))
    }

    /// Seqlock read of the latest frame, converted BGRX -> RGB.
    pub fn read(&self) -> Option<Raw> {
        let b = self.base as *const u8;
        // A copy in crosvm takes about 1 ms and the RGB pass about as long, so retry by time, not count.
        let end = std::time::Instant::now() + Duration::from_millis(200);
        while std::time::Instant::now() < end {
            let s1 = self.seq_ref().load(Ordering::Acquire);
            if s1 == 0 {
                return None;
            }
            if s1 & 1 == 1 {
                std::thread::yield_now();
                continue;
            }
            // SAFETY: header fields and pixel rows are inside the mapping (crosvm bounds them).
            let (w, h, stride, us) = unsafe {
                ((b.add(8) as *const u32).read_volatile(), (b.add(12) as *const u32).read_volatile(),
                 (b.add(16) as *const u32).read_volatile() as usize, (b.add(24) as *const u64).read_volatile())
            };
            let fourcc = unsafe { (b.add(20) as *const u32).read_volatile() };
            // Copy the raw pixels first (fast memcpy), so the window a new frame can tear is short.
            let px = unsafe { std::slice::from_raw_parts(b.add(HEADER), stride * h as usize) }.to_vec();
            fence(Ordering::Acquire);
            if self.seq_ref().load(Ordering::Relaxed) == s1 {
                let rgb = to_rgb(&px, w as usize, stride, red_first(fourcc));
                return Some(Raw { rgb: image::RgbImage::from_raw(w, h, rgb)?, seq: self.seq(), flush_us: us });
            }
        }
        None
    }
}

/// DRM fourccs whose bytes are R,G,B,x in memory (XB24, AB24). XR24/AR24 and unknown are B,G,R,x.
pub fn red_first(fourcc: u32) -> bool {
    fourcc == u32::from_le_bytes(*b"XB24") || fourcc == u32::from_le_bytes(*b"AB24")
}

/// 32-bit rows (`stride` bytes each) -> packed RGB.
pub fn to_rgb(px: &[u8], w: usize, stride: usize, red_first: bool) -> Vec<u8> {
    let (r, b) = if red_first { (0, 2) } else { (2, 0) };
    let mut rgb = Vec::with_capacity(w * (px.len() / stride.max(1)) * 3);
    for row in px.chunks_exact(stride) {
        for p in row[..w * 4].chunks_exact(4) {
            rgb.extend_from_slice(&[p[r], p[1], p[b]]);
        }
    }
    rgb
}

// SAFETY: the view is read-only shared memory, valid for the process lifetime.
unsafe impl Send for Fb {}
unsafe impl Sync for Fb {}

#[cfg(test)]
mod tests {
    use super::*;

    fn decode(b: &[u8]) -> Vec<(u16, u16, i32)> {
        b.chunks_exact(8).map(|c| (u16::from_le_bytes([c[0], c[1]]), u16::from_le_bytes([c[2], c[3]]),
            i32::from_le_bytes([c[4], c[5], c[6], c[7]]))).collect()
    }

    #[test]
    fn tap_down_up_is_protocol_b() {
        let d = decode(&touch(Some(3), 100, 200, true));
        assert_eq!(d.first(), Some(&(EV_ABS, ABS_MT_SLOT, 0)));
        assert!(d.contains(&(EV_ABS, ABS_MT_TRACKING_ID, 3)));
        assert!(d.contains(&(EV_ABS, ABS_MT_POSITION_X, 100)) && d.contains(&(EV_ABS, ABS_MT_POSITION_Y, 200)));
        assert!(d.contains(&(EV_KEY, BTN_TOUCH, 1)));
        assert_eq!(d.last(), Some(&(EV_SYN, 0, 0)));
        let m = decode(&touch(Some(3), 5, 6, false));
        assert!(!m.iter().any(|e| e.1 == ABS_MT_TRACKING_ID || e.1 == BTN_TOUCH), "a move keeps the contact");
        let u = decode(&touch(None, 0, 0, false));
        assert!(u.contains(&(EV_ABS, ABS_MT_TRACKING_ID, -1)) && u.contains(&(EV_KEY, BTN_TOUCH, 0)));
        assert_eq!(u.last(), Some(&(EV_SYN, 0, 0)));
    }

    #[test]
    fn pixel_formats() {
        let px = [1, 2, 3, 0, 4, 5, 6, 0, 9, 9, 9, 9]; // 2 pixels + 4 bytes row padding
        assert_eq!(to_rgb(&px, 2, 12, true), vec![1, 2, 3, 4, 5, 6]);
        assert_eq!(to_rgb(&px, 2, 12, false), vec![3, 2, 1, 6, 5, 4]);
        assert!(red_first(0x3432_4258) && !red_first(0x3432_5258) && !red_first(0));
    }

    #[test]
    fn keys_map() {
        assert_eq!(linux_key("KEYCODE_HOME"), Some(172));
        assert_eq!(linux_key("KEYCODE_BACK"), Some(158));
        assert_eq!(linux_key("KEYCODE_CAMERA"), None);
    }
}
