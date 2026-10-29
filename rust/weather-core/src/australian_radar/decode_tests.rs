use super::*;
use bzip2::{write::BzEncoder, Compression};
use std::io::Write;

const DAY: u32 = 20_756;
const MILLIS: u32 = 41_664_000;
const MOMENT: usize = 36;

fn volume(records: &[u8]) -> Vec<u8> {
    let mut bytes = b"AR2V0006.001".to_vec();
    bytes.extend_from_slice(&DAY.to_be_bytes());
    bytes.extend_from_slice(&(MILLIS - 240_000).to_be_bytes());
    bytes.extend_from_slice(b"ABRD");
    bytes.extend_from_slice(records);
    bytes
}

fn moment(bits: u8, scale: f32, offset: f32, values: &[u16]) -> Vec<u8> {
    let mut block = vec![0; 28];
    block[..4].copy_from_slice(b"DREF");
    block[8..10].copy_from_slice(&(values.len() as u16).to_be_bytes());
    block[10..12].copy_from_slice(&1125_u16.to_be_bytes());
    block[12..14].copy_from_slice(&250_u16.to_be_bytes());
    block[19] = bits;
    block[20..24].copy_from_slice(&scale.to_be_bytes());
    block[24..28].copy_from_slice(&offset.to_be_bytes());
    for &value in values {
        if bits == 16 {
            block.extend_from_slice(&value.to_be_bytes());
        } else {
            block.push(value as u8);
        }
    }
    block
}

fn ray(number: u16, status: u8, elevation: (u8, f32), azimuth: f32) -> Vec<u8> {
    let mut bytes = vec![0; MOMENT];
    bytes[..4].copy_from_slice(b"ABRD");
    bytes[4..8].copy_from_slice(&MILLIS.to_be_bytes());
    bytes[8..10].copy_from_slice(&(DAY as u16).to_be_bytes());
    bytes[10..12].copy_from_slice(&number.to_be_bytes());
    bytes[12..16].copy_from_slice(&azimuth.to_be_bytes());
    bytes[20] = 2;
    bytes[21] = status;
    bytes[22] = elevation.0;
    bytes[24..28].copy_from_slice(&elevation.1.to_be_bytes());
    bytes[30..32].copy_from_slice(&1_u16.to_be_bytes());
    bytes[32..36].copy_from_slice(&(MOMENT as u32).to_be_bytes());
    bytes.extend_from_slice(&moment(8, 2.0, 66.0, &[0, 1, 66, 67, 255]));
    bytes
}

fn records(rays: &[Vec<u8>]) -> Vec<u8> {
    let mut records = Vec::new();
    for ray in rays {
        let mut ray = ray.clone();
        let length = ray.len() as u16;
        ray[18..20].copy_from_slice(&length.to_be_bytes());
        if ray.len() % 2 != 0 {
            ray.push(0);
        }
        let mut header = vec![0; 28];
        header[12..14].copy_from_slice(&((ray.len() as u16 + 16) / 2).to_be_bytes());
        header[15] = 31;
        header[24..26].copy_from_slice(&1_u16.to_be_bytes());
        header[26..28].copy_from_slice(&1_u16.to_be_bytes());
        records.extend_from_slice(&header);
        records.extend_from_slice(&ray);
    }
    records
}

fn complete() -> Vec<Vec<u8>> {
    vec![ray(1, 3, (1, 0.5), 0.5), ray(360, 2, (1, 0.5), 359.5)]
}

fn replace_moments(ray: &mut Vec<u8>, blocks: &[Vec<u8>]) {
    ray.truncate(32);
    ray[30..32].copy_from_slice(&(blocks.len() as u16).to_be_bytes());
    ray.resize(32 + 4 * blocks.len(), 0);
    for (index, block) in blocks.iter().enumerate() {
        let pointer = ray.len() as u32;
        ray[32 + 4 * index..36 + 4 * index].copy_from_slice(&pointer.to_be_bytes());
        ray.extend_from_slice(block);
    }
}

fn compressed(parts: &[&[u8]]) -> Vec<u8> {
    let mut output = Vec::new();
    for (index, part) in parts.iter().enumerate() {
        let mut encoder = BzEncoder::new(Vec::new(), Compression::fast());
        encoder.write_all(part).unwrap();
        let block = encoder.finish().unwrap();
        let length = block.len() as i32;
        let signed_length = if index == parts.len() - 1 {
            -length
        } else {
            length
        };
        output.extend_from_slice(&signed_length.to_be_bytes());
        output.extend_from_slice(&block);
    }
    output
}

#[test]
fn physical_reflectivity_geometry_and_sweep_time_come_from_records() {
    let bytes = volume(&records(&complete()));
    let sweep = decode(&bytes, "reflectivity").unwrap();
    assert_eq!(sweep.site_id, "ABRD");
    // The fixture volume header is four minutes earlier than these radials.
    assert_eq!(sweep.valid_time, "2026-10-29T11:34:24Z");
    assert_eq!(sweep.elevation_angle, 0.5);
    assert_eq!(sweep.radials.len(), 2);
    let radial = &sweep.radials[0];
    assert_eq!((radial.azimuth, radial.width), (0.5, 1.0));
    assert_eq!(
        (radial.first_gate_m, radial.gate_spacing_m),
        (1125.0, 250.0)
    );
    assert!(radial.values[0].is_nan());
    assert!(radial.values[1].is_nan());
    assert_eq!(&radial.values[2..], &[0.0, 0.5, 94.5]);
    assert_eq!(sweep.radials[1].azimuth, 359.5);
}

#[test]
fn independent_compression_blocks_can_split_a_radial_header() {
    let raw = records(&complete());
    let bytes = volume(&compressed(&[&raw[..47], &raw[47..]]));
    let sweep = decode(&bytes, "reflectivity").unwrap();
    assert_eq!(sweep.radials.len(), 2);
    assert_eq!(sweep.radials[0].values[3], 0.5);
}

#[test]
fn sixteen_bit_calibration_is_not_clamped_or_quantized() {
    let mut rays = complete();
    for ray in &mut rays {
        ray.truncate(MOMENT);
        ray.extend_from_slice(&moment(16, 100.0, 4096.0, &[0, 1, 2, 4097, 65535]));
        ray[20] = 1;
    }
    let sweep = decode(&volume(&records(&rays)), "reflectivity").unwrap();
    let radial = &sweep.radials[0];
    assert_eq!(radial.width, 0.5);
    assert_eq!(&radial.values[2..], &[-40.94, 0.01, 614.39]);
}

#[test]
fn requested_product_uses_its_own_moment_calibration_and_missing_codes() {
    let reference = moment(8, 2.0, 66.0, &[0, 1, 2, 128, 129, 130, 255]);
    let mut velocity = moment(8, 2.0, 129.0, &[0, 1, 2, 128, 129, 130, 255]);
    velocity[1..4].copy_from_slice(b"VEL");
    velocity[10..12].copy_from_slice(&125_u16.to_be_bytes());
    velocity[12..14].copy_from_slice(&125_u16.to_be_bytes());
    let mut rays = complete();
    replace_moments(&mut rays[0], &[reference.clone(), velocity.clone()]);
    replace_moments(&mut rays[1], &[velocity, reference]);
    let raw = records(&rays);
    let bytes = volume(&compressed(&[&raw]));
    let reflectivity = decode(&bytes, "reflectivity").unwrap();
    let velocity = decode(&bytes, "velocity").unwrap();
    assert_eq!(velocity.valid_time, reflectivity.valid_time);
    for (reference, velocity) in reflectivity.radials.iter().zip(&velocity.radials) {
        assert_eq!(
            (reference.first_gate_m, reference.gate_spacing_m),
            (1125.0, 250.0)
        );
        assert_eq!(
            (velocity.first_gate_m, velocity.gate_spacing_m),
            (125.0, 125.0)
        );
        assert_eq!(
            (velocity.azimuth, velocity.width),
            (reference.azimuth, reference.width)
        );
        assert!(reference.values[0].is_nan());
        assert!(velocity.values[0].is_nan());
        assert!(reference.values[1].is_nan());
        assert_eq!(velocity.values[1], f32::INFINITY);
        assert_eq!(&reference.values[2..], &[-32.0, 31.0, 31.5, 32.0, 94.5]);
        assert_eq!(&velocity.values[2..], &[-63.5, -0.5, 0.0, 0.5, 63.0]);
    }
}

#[test]
fn sixteen_bit_velocity_preserves_source_meters_per_second_and_folded_code() {
    let mut velocity = moment(16, 100.0, 32768.0, &[0, 1, 2, 32767, 32768, 32769, 65535]);
    velocity[1..4].copy_from_slice(b"VEL");
    let mut rays = complete();
    for ray in &mut rays {
        replace_moments(ray, std::slice::from_ref(&velocity));
    }
    let velocity = decode(&volume(&records(&rays)), "velocity").unwrap();
    assert!(velocity.radials[0].values[0].is_nan());
    assert_eq!(velocity.radials[0].values[1], f32::INFINITY);
    assert_eq!(
        &velocity.radials[0].values[2..],
        &[-327.66, -0.01, 0.0, 0.01, 327.67]
    );
}

#[test]
fn lowest_complete_cut_is_selected_separately_for_each_product() {
    let mut rays = complete();
    let mut velocity = moment(8, 2.0, 129.0, &[0, 1, 128, 129, 130]);
    velocity[1..4].copy_from_slice(b"VEL");
    let mut higher = [ray(1, 0, (2, 1.0), 0.5), ray(360, 2, (2, 1.0), 359.5)];
    for ray in &mut higher {
        replace_moments(ray, std::slice::from_ref(&velocity));
    }
    rays.extend(higher);
    let mut incomplete = ray(1, 0, (3, 0.1), 0.5);
    replace_moments(&mut incomplete, &[velocity]);
    rays.push(incomplete);
    let bytes = volume(&records(&rays));
    assert_eq!(decode(&bytes, "reflectivity").unwrap().elevation_angle, 0.5);
    assert_eq!(decode(&bytes, "velocity").unwrap().elevation_angle, 1.0);
}

#[test]
fn lowest_complete_elevation_is_selected_in_any_order() {
    let mut rays = vec![ray(1, 3, (1, 2.0), 0.5), ray(360, 2, (1, 2.0), 359.5)];
    rays.extend([ray(1, 0, (2, 0.5), 0.5), ray(360, 2, (2, 0.5), 359.5)]);
    rays.extend([ray(1, 0, (3, 0.1), 0.5)]); // Missing end marker.
    let sweep = decode(&volume(&records(&rays)), "reflectivity").unwrap();
    assert_eq!(sweep.elevation_angle, 0.5);
}

#[test]
fn missing_sectors_are_not_filled_and_blank_radials_are_not_drawn() {
    let mut rays = complete();
    let mut blank = ray(180, 1, (1, 0.5), 180.0);
    blank[28] = 1;
    let mut bad = ray(181, 0x11, (1, 0.5), 181.0);
    bad[28] = 0;
    rays.insert(1, blank);
    rays.insert(2, bad);
    let sweep = decode(&volume(&records(&rays)), "reflectivity").unwrap();
    assert_eq!(sweep.radials.len(), 2);
    assert_eq!(
        (sweep.radials[0].azimuth, sweep.radials[1].azimuth),
        (0.5, 359.5)
    );
    assert!(sweep.radials.iter().all(|radial| radial.width == 1.0));
}

#[test]
fn incomplete_or_product_missing_sweeps_are_not_selected() {
    assert!(decode(
        &volume(&records(&[ray(1, 3, (1, 0.5), 0.5)])),
        "reflectivity"
    )
    .is_err());
    let mut rays = complete();
    for ray in &mut rays {
        ray[MOMENT + 1..MOMENT + 4].copy_from_slice(b"VEL");
    }
    assert!(decode(&volume(&records(&rays)), "reflectivity").is_err());
    assert!(decode(&volume(&records(&complete())), "velocity")
        .unwrap_err()
        .contains("no complete velocity"));
    assert!(decode(&volume(&records(&complete())), "rho")
        .unwrap_err()
        .contains("reflectivity and velocity only"));
}

#[test]
fn every_truncated_prefix_and_truncated_frame_is_rejected() {
    let bytes = volume(&records(&complete()));
    for end in 0..bytes.len() {
        assert!(
            decode(&bytes[..end], "reflectivity").is_err(),
            "prefix length {end}"
        );
    }
    let compressed = volume(&compressed(&[&records(&complete())]));
    for end in 24..compressed.len() {
        assert!(
            decode(&compressed[..end], "reflectivity").is_err(),
            "compressed prefix {end}"
        );
    }
    let mut trailing = bytes.clone();
    trailing.push(0);
    assert!(decode(&trailing, "reflectivity").is_err());
}

#[test]
fn malformed_offsets_counts_geometry_and_calibration_are_errors() {
    let cases: &[(usize, &[u8])] = &[
        (0, b"ZZZZ"),
        (4, &86_400_000_u32.to_be_bytes()),
        (8, &0_u16.to_be_bytes()),
        (10, &0_u16.to_be_bytes()),
        (10, &721_u16.to_be_bytes()),
        (12, &f32::NAN.to_be_bytes()),
        (12, &360_f32.to_be_bytes()),
        (16, &[1]),
        (20, &[0]),
        (21, &[6]),
        (22, &[0]),
        (24, &f32::INFINITY.to_be_bytes()),
        (30, &11_u16.to_be_bytes()),
        (32, &35_u32.to_be_bytes()),
        (32, &u32::MAX.to_be_bytes()),
        (MOMENT + 8, &8193_u16.to_be_bytes()),
        (MOMENT + 12, &0_u16.to_be_bytes()),
        (MOMENT + 19, &[32]),
        (MOMENT + 20, &0_f32.to_be_bytes()),
        (MOMENT + 20, &f32::NAN.to_be_bytes()),
        (MOMENT + 20, &f32::MIN_POSITIVE.to_be_bytes()),
        (MOMENT + 24, &f32::INFINITY.to_be_bytes()),
    ];
    for (offset, replacement) in cases {
        let mut rays = complete();
        rays[0][*offset..*offset + replacement.len()].copy_from_slice(replacement);
        assert!(
            decode(&volume(&records(&rays)), "reflectivity").is_err(),
            "field {offset}: {replacement:?}"
        );
    }
    let mut raw = records(&complete());
    raw[24..26].copy_from_slice(&2_u16.to_be_bytes());
    assert!(decode(&volume(&raw), "reflectivity").is_err());
    let mut rays = complete();
    rays[1][10..12].copy_from_slice(&1_u16.to_be_bytes());
    assert!(decode(&volume(&records(&rays)), "reflectivity").is_err());
    for second_pointer in [40_u32, 41] {
        let mut rays = complete();
        rays[0].splice(MOMENT..MOMENT, [0; 4]);
        rays[0][30..32].copy_from_slice(&2_u16.to_be_bytes());
        rays[0][32..36].copy_from_slice(&40_u32.to_be_bytes());
        rays[0][36..40].copy_from_slice(&second_pointer.to_be_bytes());
        assert!(decode(&volume(&records(&rays)), "reflectivity").is_err());
    }
    let mut raw = records(&complete());
    raw[12..14].copy_from_slice(&u16::MAX.to_be_bytes());
    raw[24..28].copy_from_slice(&u32::MAX.to_be_bytes());
    assert!(decode(&volume(&raw), "reflectivity").is_err());
}

#[test]
fn compression_crc_lengths_trailing_data_and_byte_limit_are_enforced() {
    let raw = records(&complete());
    let packed = compressed(&[&raw]);
    let mut broken_crc = packed.clone();
    broken_crc[15] ^= 1;
    assert!(unpack(&broken_crc, MAX_DECOMPRESSED_BYTES).is_err());
    let mut broken_length = packed.clone();
    broken_length[..4].copy_from_slice(&i32::MIN.to_be_bytes());
    assert!(unpack(&broken_length, MAX_DECOMPRESSED_BYTES).is_err());
    let mut trailing = packed.clone();
    trailing.push(0);
    trailing[..4].copy_from_slice(&(-((packed.len() - 3) as i32)).to_be_bytes());
    assert!(unpack(&trailing, MAX_DECOMPRESSED_BYTES).is_err());
    assert!(unpack(&packed, raw.len() - 1)
        .unwrap_err()
        .contains("limit"));
    assert_eq!(unpack(&packed, raw.len()).unwrap().as_ref(), raw);
    let mut after_terminal = packed.clone();
    after_terminal.extend_from_slice(&packed);
    assert!(unpack(&after_terminal, MAX_DECOMPRESSED_BYTES).is_err());
}

#[test]
#[ignore = "opt-in: set MRRL_AR2V_SAMPLE to a downloaded ABRD_20261029_113000.ar2v"]
fn real_abrd_archive_ii_sample() {
    let path = std::env::var_os("MRRL_AR2V_SAMPLE")
        .expect("MRRL_AR2V_SAMPLE must name the real ABRD sample");
    let bytes = std::fs::read(path).unwrap();
    assert_eq!(bytes.len(), 840_218);
    let start = std::time::Instant::now();
    let sweep = decode(&bytes, "reflectivity").unwrap();
    assert_eq!(sweep.site_id, "ABRD");
    assert_eq!(sweep.valid_time, "2026-10-29T11:34:24.042Z");
    assert_eq!(sweep.elevation_angle, 0.5);
    assert_eq!(sweep.radials.len(), 360);
    assert!(sweep.radials.iter().all(|ray| ray.values.len() == 1284
        && ray.first_gate_m == 1125.0
        && ray.gate_spacing_m == 250.0
        && ray.width == 1.0));
    let unique: std::collections::BTreeSet<_> = sweep
        .radials
        .iter()
        .flat_map(|ray| &ray.values)
        .filter(|value| value.is_finite())
        .map(|value| value.to_bits())
        .collect();
    assert_eq!(unique.len(), 51);
    let decode_elapsed = start.elapsed();
    let velocity_start = std::time::Instant::now();
    let velocity = decode(&bytes, "velocity").unwrap();
    let velocity_elapsed = velocity_start.elapsed();
    assert_eq!(velocity.site_id, sweep.site_id);
    assert_eq!(velocity.valid_time, sweep.valid_time);
    assert_eq!(velocity.elevation_angle, 0.5);
    assert_eq!(velocity.radials.len(), 360);
    assert!(velocity.radials.iter().all(|ray| ray.values.len() == 1284
        && ray.first_gate_m == 1125.0
        && ray.gate_spacing_m == 250.0
        && ray.width == 1.0));
    let velocity_values: std::collections::BTreeSet<_> = velocity
        .radials
        .iter()
        .flat_map(|ray| &ray.values)
        .filter(|value| value.is_finite())
        .map(|value| value.to_bits())
        .collect();
    assert!(velocity_values.len() <= 254);
    let raw = unpack(&bytes[24..], MAX_DECOMPRESSED_BYTES).unwrap();
    let mut frame_offset = 0;
    let mut angles = std::collections::BTreeSet::new();
    let mut moments = std::collections::BTreeMap::new();
    let mut acquisitions = Vec::new();
    let mut low_acquisitions = Vec::new();
    while frame_offset < raw.len() {
        let header = &raw[frame_offset + 12..frame_offset + 28];
        let length = if matches!(header[3], 29 | 31) {
            12 + usize::from(u16_at(header, 0).unwrap()) * 2
        } else {
            2432
        };
        if header[3] == 31 {
            let body = &raw[frame_offset + 28..frame_offset + length];
            let ray = read_ray(body, b"ABRD", b"REF").unwrap();
            angles.insert((ray.angle * 10.0).round() as i32);
            acquisitions.push(ray.time);
            if ray.elevation == 1 {
                low_acquisitions.push(ray.time);
            }
            for index in 0..usize::from(u16_at(body, 30).unwrap()) {
                let pointer = u32_at(body, 32 + index * 4).unwrap() as usize;
                if pointer != 0 && body[pointer] == b'D' {
                    let name = String::from_utf8(body[pointer + 1..pointer + 4].to_vec()).unwrap();
                    if name == "VEL" && ray.elevation == 1 {
                        let block = &body[pointer..];
                        assert_eq!(block[19], 8);
                        assert_eq!(
                            (f32_at(block, 20).unwrap(), f32_at(block, 24).unwrap()),
                            (2.0, 129.0)
                        );
                        assert_eq!(u16_at(block, 8).unwrap(), 1284);
                        let azimuth = f64::from(f32_at(body, 12).unwrap());
                        let radial_index = velocity
                            .radials
                            .binary_search_by(|radial| radial.azimuth.total_cmp(&azimuth))
                            .unwrap();
                        let decoded = &velocity.radials[radial_index];
                        for (&raw, &value) in block[28..28 + 1284].iter().zip(&decoded.values) {
                            match raw {
                                0 => assert!(value.is_nan()),
                                1 => assert_eq!(value, f32::INFINITY),
                                _ => assert_eq!(value, (f32::from(raw) - 129.0) / 2.0),
                            }
                        }
                    }
                    *moments.entry(name).or_insert(0_usize) += 1;
                }
            }
        }
        frame_offset += length;
    }
    assert_eq!(angles.len(), 13);
    assert_eq!((angles.first(), angles.last()), (Some(&5), Some(&320)));
    assert_eq!(
        moments,
        [
            ("REF".to_string(), 4680),
            ("SW ".to_string(), 4680),
            ("VEL".to_string(), 4680)
        ]
        .into()
    );
    println!("volume: elevation_tenths={angles:?}, moments={moments:?}, acquisition={} through {}, selected_acquisition={} through {}",
        acquisitions.iter().min().unwrap().format(&Rfc3339).unwrap(), acquisitions.iter().max().unwrap().format(&Rfc3339).unwrap(),
        low_acquisitions.iter().min().unwrap().format(&Rfc3339).unwrap(), low_acquisitions.iter().max().unwrap().format(&Rfc3339).unwrap());
    println!("ABRD native decode: time={} elevation={} radials={} gates={} finite_values={} first_azimuth={} last_azimuth={} elapsed={:?}",
        sweep.valid_time, sweep.elevation_angle, sweep.radials.len(), sweep.radials[0].values.len(), unique.len(),
        sweep.radials[0].azimuth, sweep.radials.last().unwrap().azimuth, decode_elapsed);
    let values = velocity.radials.iter().flat_map(|ray| &ray.values);
    let minimum = values
        .clone()
        .filter(|value| value.is_finite())
        .min_by(|a, b| a.total_cmp(b))
        .unwrap();
    let maximum = values
        .clone()
        .filter(|value| value.is_finite())
        .max_by(|a, b| a.total_cmp(b))
        .unwrap();
    println!("ABRD velocity native decode: time={} elevation={} radials={} finite_values={} minimum_mps={} maximum_mps={} missing={} folded={} elapsed={:?}",
        velocity.valid_time, velocity.elevation_angle, velocity.radials.len(), velocity_values.len(), minimum, maximum,
        values.clone().filter(|value| value.is_nan()).count(), values.filter(|value| **value == f32::INFINITY).count(), velocity_elapsed);
}
