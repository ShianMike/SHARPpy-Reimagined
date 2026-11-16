//! Optional integration check of the files produced by the real browser flow.
use std::{
    hash::{Hash, Hasher},
    io::Cursor,
    path::PathBuf,
};

#[test]
#[ignore = "requires SHARPPY_EXPORT_ARTIFACTS containing the real local UI exports"]
fn browser_exports_decode_as_distinct_lossless_looping_frames() {
    let directory = PathBuf::from(
        std::env::var("SHARPPY_EXPORT_ARTIFACTS").expect("Set the export evidence directory"),
    );
    for (name, dimensions) in [
        ("sounding-severe.webp", (4564, 3077)),
        ("map-layout-2.webp", (1920, 1080)),
    ] {
        let bytes = std::fs::read(directory.join(name)).unwrap();
        let mut image = image_webp::WebPDecoder::new(Cursor::new(bytes)).unwrap();
        assert!(image.is_animated(), "{name}");
        assert!(!image.is_lossy(), "{name}");
        assert_eq!(image.dimensions(), dimensions, "{name}");
        assert_eq!(image.num_frames(), 3, "{name}");
        assert_eq!(image.loop_duration(), 2250, "{name}");
        assert!(matches!(image.loop_count(), image_webp::LoopCount::Forever));
        let mut pixels = vec![0; image.output_buffer_size().unwrap()];
        let mut hashes = std::collections::HashSet::new();
        for _ in 0..3 {
            assert_eq!(image.read_frame(&mut pixels).unwrap(), 750, "{name}");
            let mut hash = std::collections::hash_map::DefaultHasher::new();
            pixels.hash(&mut hash);
            hashes.insert(hash.finish());
        }
        assert_eq!(hashes.len(), 3, "Forecast hours must be distinct in {name}");
    }
}
