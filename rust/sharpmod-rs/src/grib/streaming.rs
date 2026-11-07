//! Opportunistic point preparation from complete messages during a validated download.
use super::*;
use ring::digest::{Context, SHA256};
use std::io::Read;

/// One record buffer per process; other requests keep the ordinary file path.
static STREAM_SLOT: Mutex<()> = Mutex::new(());
const MAX_MESSAGE_BYTES: usize = 64 * 1024 * 1024;

#[derive(Clone)]
pub(crate) struct Request {
    library: PathBuf,
    point: (f64, f64),
    missing: Option<f64>,
    generation: u64,
}

pub(crate) struct Prepared {
    request: Request,
    digest: Context,
    bytes: usize,
    decoder: messages::PointDecoder,
}

impl Request {
    pub const MIN_BYTES: u64 = point_cache::MIN_FILE_BYTES as u64;

    pub fn new(
        library: PathBuf,
        latitude: f64,
        longitude: f64,
        missing: Option<f64>,
    ) -> Result<Option<Self>, GribError> {
        let point = validate_and_normalize_point(latitude, longitude)?;
        let cache = inventory_cache();
        Ok(cache.enabled.then_some(Self {
            library,
            point,
            missing,
            generation: cache.generation,
        }))
    }

    pub fn decode(self, input: &mut dyn Read) -> Result<Prepared, GribError> {
        let _slot = STREAM_SLOT
            .try_lock()
            .map_err(|_| GribError::new("another GRIB stream is preparing a point"))?;
        let coordinates = [self.point];
        let mut decoder = messages::PointDecoder::new(&coordinates, self.missing);
        let mut digest = Context::new(&SHA256);
        let mut buffer = Vec::new();
        let mut bytes = 0;
        let mut count = 0;
        while let Some(boundary) = read_message(input, &mut buffer, bytes)? {
            digest.update(&buffer);
            // Never hold the ecCodes lock while waiting for network bytes.
            let _guard = ECCODES_CALL_LOCK
                .lock()
                .map_err(|_| GribError::new("ecCodes call lock was poisoned"))?;
            let api = EccodesApi::cached(&self.library)?;
            let _multi = api.enable_multi_support();
            decoder.message(api, &buffer, boundary)?;
            bytes += buffer.len();
            count += 1;
        }
        if count == 0 {
            return Err(GribError::new("no complete GRIB messages were found"));
        }
        Ok(Prepared {
            request: self,
            digest,
            bytes,
            decoder,
        })
    }
}

impl Prepared {
    /// Verify the downloaded prefix and incorporate any later surface companion.
    /// No prepared value is visible until the complete final source is available.
    pub fn finish(mut self, path: &Path) -> Result<bool, GribError> {
        {
            let cache = inventory_cache();
            if !cache.enabled || cache.generation != self.request.generation {
                return Ok(false);
            }
        }
        let (_file, mapping, _) = open_grib_mapping(path)?;
        if mapping.len() < self.bytes
            || point_cache::fingerprint(&mapping[..self.bytes]).as_slice()
                != self.digest.clone().finish().as_ref()
        {
            return Ok(false);
        }
        if mapping.len() > self.bytes {
            let suffix = &mapping[self.bytes..];
            let boundaries = scan_message_boundaries(suffix)?;
            let _guard = ECCODES_CALL_LOCK
                .lock()
                .map_err(|_| GribError::new("ecCodes call lock was poisoned"))?;
            let api = EccodesApi::cached(&self.request.library)?;
            let _multi = api.enable_multi_support();
            for mut boundary in boundaries {
                let message = &suffix[boundary.offset..boundary.offset + boundary.length];
                boundary.offset += self.bytes;
                self.decoder.message(api, message, boundary)?;
            }
            self.digest.update(suffix);
        }
        let point = assemble_decoded_points(self.decoder.states, self.request.missing)?
            .pop()
            .expect("one requested GRIB point");
        let content = self
            .digest
            .finish()
            .as_ref()
            .try_into()
            .expect("SHA-256 has 32 bytes");
        let mut cache = inventory_cache();
        if cache.enabled && cache.generation == self.request.generation {
            cache.decoded_points.insert(
                point_cache::Key::new(content, self.request.point, self.request.missing),
                &point,
            );
            return Ok(true);
        }
        Ok(false)
    }
}

fn read_message(
    input: &mut dyn Read,
    buffer: &mut Vec<u8>,
    offset: usize,
) -> Result<Option<MessageBoundary>, GribError> {
    let read_error = |error| GribError::new(format!("GRIB stream at byte {offset}: {error}"));
    let mut header = [0; 16];
    if input.read(&mut header[..1]).map_err(read_error)? == 0 {
        return Ok(None);
    }
    input.read_exact(&mut header[1..8]).map_err(read_error)?;
    if &header[..4] != b"GRIB" {
        return Err(GribError::new(format!(
            "GRIB stream has an invalid indicator at byte {offset}"
        )));
    }
    let edition = header[7];
    let (length, header_len, minimum) = match edition {
        1 => (read_be_u24(&header[4..7]), 8, 12),
        2 => {
            input.read_exact(&mut header[8..]).map_err(read_error)?;
            (read_be_u64(&header[8..])?, 16, 20)
        }
        value => {
            return Err(GribError::new(format!(
                "unsupported GRIB edition {value} at byte {offset}"
            )))
        }
    };
    if !(minimum..=MAX_MESSAGE_BYTES).contains(&length) {
        return Err(GribError::new(format!(
            "GRIB{edition} length {length} is outside the streaming buffer budget"
        )));
    }
    buffer.resize(length, 0);
    buffer[..header_len].copy_from_slice(&header[..header_len]);
    input
        .read_exact(&mut buffer[header_len..])
        .map_err(read_error)?;
    if &buffer[length - 4..] != b"7777" {
        return Err(GribError::new(format!(
            "corrupt GRIB{edition} message at byte {offset}: missing 7777 terminator"
        )));
    }
    Ok(Some(MessageBoundary {
        offset,
        length,
        edition,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{self, Cursor};

    struct Split(Cursor<Vec<u8>>);
    impl Read for Split {
        fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
            let length = out.len().min(3);
            self.0.read(&mut out[..length])
        }
    }

    #[test]
    fn framing_preserves_complete_records_across_arbitrary_http_chunks() {
        let first = b"GRIB\0\0\x0c\x017777";
        let second = b"GRIB\0\0\0\x02\0\0\0\0\0\0\0\x147777";
        let mut wire = first.to_vec();
        wire.extend(second);
        let mut input = Split(Cursor::new(wire.clone()));
        let mut buffer = Vec::new();
        let mut offset = 0;
        for expected in [first.as_slice(), second.as_slice()] {
            let boundary = read_message(&mut input, &mut buffer, offset)
                .unwrap()
                .unwrap();
            assert_eq!(boundary.offset, offset);
            assert_eq!(buffer, expected);
            offset += expected.len();
        }
        assert!(read_message(&mut input, &mut buffer, offset)
            .unwrap()
            .is_none());
        for end in 1..wire.len() {
            if end == first.len() {
                continue;
            }
            let mut input = Split(Cursor::new(wire[..end].to_vec()));
            let mut offset = 0;
            loop {
                match read_message(&mut input, &mut buffer, offset) {
                    Ok(Some(boundary)) => offset += boundary.length,
                    Err(_) => break,
                    Ok(None) => panic!("a truncated record was accepted at {end}"),
                }
            }
        }
    }

    #[test]
    fn corrupt_and_oversized_records_never_reach_the_decoder() {
        let mut buffer = Vec::new();
        for wire in [
            b"GRIB\0\0\x0c\x017776".to_vec(),
            b"GRIB\0\0\0\x02\0\0\0\0\x7f\xff\xff\xff".to_vec(),
            b"GRIB\0\0\0\x03".to_vec(),
        ] {
            assert!(read_message(&mut Cursor::new(wire), &mut buffer, 0).is_err());
        }
        assert!(buffer.capacity() < 1024);
    }

    #[test]
    #[ignore = "requires the complete ECMWF fixture and ecCodes runtime"]
    fn real_stream_preserves_companions_rejects_mutation_and_respects_clear() {
        use std::io::{Seek, SeekFrom, Write};
        let fixture = PathBuf::from(std::env::var_os("SHARPMOD_GRIB_BENCH_FIXTURE").unwrap());
        let library = PathBuf::from(std::env::var_os("SHARPMOD_ECCODES_LIBRARY").unwrap());
        set_grib_inventory_cache_enabled(true);
        clear_grib_inventory_cache(true);
        let reference =
            decode_grib_point(&fixture, &library, -27.1173, -52.4449, Some(-9999.0)).unwrap();
        let (_file, mapping, _) = open_grib_mapping(&fixture).unwrap();
        let boundaries = scan_message_boundaries(&mapping).unwrap();
        let split = boundaries.last().unwrap().offset;
        assert!(split >= point_cache::MIN_FILE_BYTES);
        let prepare = || {
            Request::new(library.clone(), -27.1173, -52.4449, Some(-9999.0))
                .unwrap()
                .unwrap()
                .decode(&mut &mapping[..split])
                .unwrap()
        };

        clear_grib_inventory_cache(true);
        assert!(prepare().finish(&fixture).unwrap());
        assert!(inventory_cache().entries.is_empty());
        let cached =
            decode_grib_point(&fixture, &library, -27.1173, -52.4449, Some(-9999.0)).unwrap();
        assert_eq!(cached, reference);
        assert!(
            inventory_cache().entries.is_empty(),
            "the ordinary decoder must reuse the streamed column"
        );

        let prepared = prepare();
        clear_grib_inventory_cache(true);
        assert!(!prepared.finish(&fixture).unwrap());
        const MISSING: Option<f64> = Some(-9999.0);
        let key = point_cache::Key::new(
            point_cache::fingerprint(&mapping),
            validate_and_normalize_point(-27.1173, -52.4449).unwrap(),
            MISSING,
        );
        assert!(inventory_cache().decoded_points.get(&key).is_none());
        assert!(prepare().finish(&fixture).unwrap());

        let prepared = prepare();
        let nonce = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let changed = std::env::temp_dir().join(format!(
            "sharpmod-stream-{}-{nonce}.grib2",
            std::process::id()
        ));
        let mut output = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(&changed)
            .unwrap();
        std::io::copy(&mut &mapping[..], &mut output).unwrap();
        output.flush().unwrap();
        let before = output.metadata().unwrap();
        let times = std::fs::FileTimes::new().set_modified(before.modified().unwrap());
        output
            .seek(SeekFrom::Start(
                (boundaries[0].offset + boundaries[0].length - 1) as u64,
            ))
            .unwrap();
        output.write_all(b"6").unwrap();
        output.flush().unwrap();
        output.set_times(times).unwrap();
        let after = output.metadata().unwrap();
        assert_eq!(before.len(), after.len());
        assert_eq!(before.modified().unwrap(), after.modified().unwrap());
        drop(output);
        assert!(!prepared.finish(&changed).unwrap());
        assert!(decode_grib_point(&changed, &library, -27.1173, -52.4449, Some(-9999.0)).is_err());
        std::fs::remove_file(changed).unwrap();
    }

    #[test]
    fn only_one_pending_stream_owns_a_record_buffer_and_failure_releases_it() {
        use std::sync::{Arc, Condvar};
        struct Waiting(Arc<(Mutex<(bool, bool)>, Condvar)>);
        impl Read for Waiting {
            fn read(&mut self, _target: &mut [u8]) -> io::Result<usize> {
                let mut state = self.0 .0.lock().unwrap();
                state.0 = true;
                self.0 .1.notify_all();
                let state = self
                    .0
                     .1
                    .wait_timeout_while(state, std::time::Duration::from_secs(2), |state| !state.1)
                    .unwrap()
                    .0;
                assert!(state.1, "waiting stream did not get released");
                Err(io::Error::new(
                    io::ErrorKind::ConnectionAborted,
                    "test cancelled",
                ))
            }
        }
        let gate = Arc::new((Mutex::new((false, false)), Condvar::new()));
        std::thread::scope(|scope| {
            let producer_gate = gate.clone();
            let first = scope.spawn(move || {
                Request::new(PathBuf::new(), 35.0, -97.0, None)
                    .unwrap()
                    .unwrap()
                    .decode(&mut Waiting(producer_gate))
                    .is_err()
            });
            let state = gate
                .1
                .wait_timeout_while(
                    gate.0.lock().unwrap(),
                    std::time::Duration::from_secs(2),
                    |state| !state.0,
                )
                .unwrap()
                .0;
            assert!(state.0);
            drop(state);
            let second = Request::new(PathBuf::new(), 35.0, -97.0, None)
                .unwrap()
                .unwrap()
                .decode(&mut io::empty());
            assert!(second
                .err()
                .unwrap()
                .to_string()
                .contains("another GRIB stream"));
            gate.0.lock().unwrap().1 = true;
            gate.1.notify_all();
            assert!(first.join().unwrap());
        });
        assert!(Request::new(PathBuf::new(), 35.0, -97.0, None)
            .unwrap()
            .unwrap()
            .decode(&mut io::empty())
            .err()
            .unwrap()
            .to_string()
            .contains("no complete"));
    }
}
