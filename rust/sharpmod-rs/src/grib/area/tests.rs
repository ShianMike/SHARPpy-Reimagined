use super::*;

#[test]
#[ignore = "requires a real SHARPMOD_GRIB_BENCH_FIXTURE and SHARPMOD_ECCODES_LIBRARY"]
fn configured_native_grid_matches_eccodes_indexes_and_point_columns() {
    let path = PathBuf::from(std::env::var_os("SHARPMOD_GRIB_BENCH_FIXTURE").unwrap());
    let library = PathBuf::from(std::env::var_os("SHARPMOD_ECCODES_LIBRARY").unwrap());
    let bounds = Bounds::new(-97.46, 35.16, -97.42, 35.20).unwrap();
    let (_file, mapping, _) = open_grib_mapping(&path).unwrap();
    let (cells, decoded, kind) = {
        let _lock = ECCODES_CALL_LOCK.lock().unwrap();
        let api = EccodesApi::cached(&library).unwrap();
        let _multi = api.enable_multi_support();
        let mut decoder = messages::PointDecoder::new(&[], Some(-9999.0));
        let messages = scan_message_boundaries(&mapping)
            .unwrap()
            .into_iter()
            .filter_map(|boundary| {
                decoder
                    .message(
                        api,
                        &mapping[boundary.offset..boundary.offset + boundary.length],
                        boundary,
                    )
                    .unwrap()
            })
            .collect();
        let inventory = GribInventory {
            messages,
            content: None,
        };
        let (grid, key, kind) = grid::read(api, &mapping, &inventory).unwrap();
        let cells = grid.select(bounds, Limits::default(), &|| false).unwrap();
        let message = inventory
            .messages
            .iter()
            .find(|message| {
                message
                    .fields
                    .iter()
                    .any(|field| matches!(field.pressure_field, Some((_, FieldKind::Temperature))))
            })
            .unwrap();
        let bytes =
            &mapping[message.boundary.offset..message.boundary.offset + message.boundary.length];
        let mut cursor = bytes.as_ptr().cast_mut().cast();
        let mut remaining = bytes.len();
        let handle = api
            .next_multi_handle(bytes, &mut cursor, &mut remaining)
            .unwrap()
            .unwrap();
        for cell in &cells {
            let point =
                find_nearest_wrapped(api, handle.as_ptr(), cell.latitude, cell.longitude).unwrap();
            assert_eq!(cell.index, point.index as usize, "native index on {kind}");
            assert!(
                (cell.latitude - point.latitude).abs() < 3e-5,
                "native latitude on {kind}"
            );
            assert!(
                (normalize_longitude(cell.longitude - point.longitude)).abs() < 3e-5,
                "native longitude on {kind}"
            );
        }
        let (decoded, _, _, _) =
            columns::decode(api, &mapping, &inventory, &key, &cells, &|| false).unwrap();
        (cells, decoded, kind)
    };
    let coordinates: Vec<_> = cells
        .iter()
        .map(|cell| (cell.latitude, cell.longitude))
        .collect();
    let reference = decode_grib_points(&path, &library, &coordinates, Some(-9999.0)).unwrap();
    for (column, reference) in decoded.iter().zip(&reference) {
        assert_eq!(column.level_count, reference.level_count);
        assert_eq!(column.surface_merged, reference.surface_merged);
        for (a, b) in column.matrix.iter().zip(&reference.matrix) {
            assert!(
                (a - b).abs() < 1e-6,
                "native index decode differs from point decode: {a} {b}"
            );
        }
    }
    eprintln!("{kind}: {} native columns, {} verified surfaces; native indexes and field values match ecCodes", cells.len(), decoded.iter().filter(|point| point.surface_merged).count());
    if std::env::var_os("SHARPMOD_BOX_REQUIRE_SURFACE").is_some() {
        let output = decode(&path, &library, bounds, Limits::default(), &|| false).unwrap();
        assert_eq!(output.averaged.selected_columns, cells.len());
        assert!(output.averaged.profile.surface_merged);
        assert!(output.averaged.profile.level_count > 10);
        let repeat = decode(&path, &library, bounds, Limits::default(), &|| false).unwrap();
        assert!(repeat.cache_hit);
        assert_eq!(
            output.averaged.profile.matrix,
            repeat.averaged.profile.matrix
        );
        assert!(matches!(
            decode(&path, &library, bounds, Limits::default(), &|| true),
            Err(Error::Cancelled)
        ));
        clear_grib_inventory_cache(false);
        let concurrent = std::thread::scope(|scope| {
            let first = scope
                .spawn(|| decode(&path, &library, bounds, Limits::default(), &|| false).unwrap());
            let second = scope
                .spawn(|| decode(&path, &library, bounds, Limits::default(), &|| false).unwrap());
            (first.join().unwrap(), second.join().unwrap())
        });
        assert_ne!(
            concurrent.0.cache_hit, concurrent.1.cache_hit,
            "concurrent identical boxes should unpack once and share the completed profile"
        );
        assert_eq!(
            concurrent.0.averaged.profile.matrix,
            concurrent.1.averaged.profile.matrix
        );
        eprintln!(
            "Native box: {} levels, {} valid columns, {:?}",
            output.averaged.profile.level_count, output.averaged.valid_columns, output.timings
        );
    }
}
