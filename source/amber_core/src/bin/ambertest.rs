//! Headless harness for amber's player core.
//!
//! The FFGL plugin cannot be tested without Resolume, and driving Resolume's
//! GUI with synthesized input is forbidden in this fleet (it has modified a
//! live composition before). So everything that can be proven without a host is
//! proven here instead: that a real SWF opens, advances on wall-clock time,
//! renders non-blank frames, and -- the one that matters most -- that rendering
//! the same instant twice does not advance the movie.
//!
//! Usage: ambertest <file.swf> [--frames N] [--seq PREFIX]

use std::path::PathBuf;
use std::time::Instant;

use amber_core::AmberPlayer;

fn main() -> Result<(), String> {
    let mut args = std::env::args().skip(1);
    let path = PathBuf::from(args.next().ok_or("usage: ambertest <file.swf>")?);

    let mut frames = 60u32;
    let mut seq: Option<String> = None;
    while let Some(flag) = args.next() {
        match flag.as_str() {
            "--frames" => frames = args.next().and_then(|v| v.parse().ok()).unwrap_or(60),
            "--seq" => seq = args.next(),
            other => return Err(format!("unknown flag {other}")),
        }
    }

    let started = Instant::now();
    let player = AmberPlayer::open(&path, 550, 400)?;
    println!(
        "opened {} in {:?}: {}x{} @ {}fps, {} frames declared",
        path.display(),
        started.elapsed(),
        player.width(),
        player.height(),
        player.frame_rate(),
        player.total_frames()
    );

    player.force_play();

    let pixels = (player.width() * player.height() * 4) as usize;
    let mut buffer = vec![0u8; pixels];

    // --- the double-render guard -------------------------------------------
    // Render the same instant twice and assert the picture does not change.
    // This is the check that would have caught the class of bug documented in
    // coinop: a stateful plugin that ticks per render runs at double speed
    // whenever Resolume's preview monitor happens to be open.
    let dt = 1.0 / player.frame_rate();
    player.advance(dt);
    player.render_into(&mut buffer)?;
    let first = buffer.clone();

    player.advance(0.0); // no time has passed: must not step
    player.render_into(&mut buffer)?;
    if buffer != first {
        return Err("FAIL: a zero-elapsed advance changed the picture".into());
    }
    player.advance(-1.0); // nor may a negative one
    player.render_into(&mut buffer)?;
    if buffer != first {
        return Err("FAIL: a negative advance changed the picture".into());
    }
    println!("double-render guard: OK (zero and negative dt do not step)");

    // --- real-time playback -------------------------------------------------
    let mut non_blank = 0u32;
    let mut changed = 0u32;
    let mut previous = first;
    let render_started = Instant::now();

    for index in 0..frames {
        player.advance(dt);
        player.render_into(&mut buffer)?;

        // "Non-blank" means some pixel differs from the top-left one. A stage
        // that renders a single flat colour is the signature of content that
        // never started -- exactly what --force-play exists to prevent -- and
        // it would otherwise pass every structural check.
        let corner = &buffer[0..4];
        if buffer.chunks_exact(4).any(|pixel| pixel != corner) {
            non_blank += 1;
        }
        if buffer != previous {
            changed += 1;
        }
        previous = buffer.clone();

        if let Some(prefix) = &seq {
            write_png(&format!("{prefix}{index:04}.png"), &buffer, player.width(), player.height())?;
        }
    }

    let elapsed = render_started.elapsed();
    println!(
        "rendered {frames} frames in {:?} ({:.1} fps), {non_blank} non-blank, {changed} changed",
        elapsed,
        frames as f64 / elapsed.as_secs_f64()
    );

    if non_blank == 0 {
        return Err("FAIL: every frame was a flat colour -- content never started".into());
    }
    if changed == 0 {
        return Err("FAIL: the picture never changed across the run".into());
    }
    println!("ambertest: OK");
    Ok(())
}

/// Minimal PNG writer so the harness has no image-crate dependency of its own.
fn write_png(path: &str, rgba: &[u8], width: u32, height: u32) -> Result<(), String> {
    use std::io::Write;

    fn crc32(data: &[u8]) -> u32 {
        let mut table = [0u32; 256];
        for (index, entry) in table.iter_mut().enumerate() {
            let mut value = index as u32;
            for _ in 0..8 {
                value = if value & 1 != 0 { 0xEDB8_8320 ^ (value >> 1) } else { value >> 1 };
            }
            *entry = value;
        }
        let mut crc = 0xFFFF_FFFFu32;
        for byte in data {
            crc = table[((crc ^ *byte as u32) & 0xFF) as usize] ^ (crc >> 8);
        }
        crc ^ 0xFFFF_FFFF
    }

    fn adler32(data: &[u8]) -> u32 {
        let (mut a, mut b) = (1u32, 0u32);
        for byte in data {
            a = (a + *byte as u32) % 65521;
            b = (b + a) % 65521;
        }
        (b << 16) | a
    }

    // Raw scanlines with a zero filter byte each, then stored (uncompressed)
    // deflate blocks. Slow and large, which is fine for a diagnostic dump.
    let mut raw = Vec::with_capacity((width * height * 4 + height) as usize);
    for row in 0..height as usize {
        raw.push(0u8);
        let start = row * width as usize * 4;
        raw.extend_from_slice(&rgba[start..start + width as usize * 4]);
    }

    let mut z = vec![0x78, 0x01];
    for (index, chunk) in raw.chunks(65535).enumerate() {
        let last = if (index + 1) * 65535 >= raw.len() { 1u8 } else { 0 };
        z.push(last);
        z.extend_from_slice(&(chunk.len() as u16).to_le_bytes());
        z.extend_from_slice(&(!(chunk.len() as u16)).to_le_bytes());
        z.extend_from_slice(chunk);
    }
    z.extend_from_slice(&adler32(&raw).to_be_bytes());

    let mut out = Vec::new();
    out.extend_from_slice(&[0x89, b'P', b'N', b'G', 0x0D, 0x0A, 0x1A, 0x0A]);

    let mut chunk = |kind: &[u8; 4], payload: &[u8], out: &mut Vec<u8>| {
        out.extend_from_slice(&(payload.len() as u32).to_be_bytes());
        let mut body = kind.to_vec();
        body.extend_from_slice(payload);
        out.extend_from_slice(&body);
        out.extend_from_slice(&crc32(&body).to_be_bytes());
    };

    let mut ihdr = Vec::new();
    ihdr.extend_from_slice(&width.to_be_bytes());
    ihdr.extend_from_slice(&height.to_be_bytes());
    ihdr.extend_from_slice(&[8, 6, 0, 0, 0]); // 8-bit RGBA
    chunk(b"IHDR", &ihdr, &mut out);
    chunk(b"IDAT", &z, &mut out);
    chunk(b"IEND", &[], &mut out);

    std::fs::File::create(path)
        .and_then(|mut file| file.write_all(&out))
        .map_err(|e| format!("{path}: {e}"))
}
