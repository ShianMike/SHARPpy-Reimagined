use super::super::*;

#[test]
fn saturated_ground_uses_the_existing_surface_contract_tolerance() {
    for (excess, accepted) in [(0.0, true), (0.04, true), (0.5, true), (0.51, false)] {
        let mut assembler = RecordAssembler::default();
        for (i, p) in [900.0, 800.0].into_iter().enumerate() {
            assembler.insert(p, FieldKind::Temperature, 280.0, i);
            assembler.insert(
                p,
                FieldKind::GeopotentialHeight,
                1000.0 + i as f64 * 1000.0,
                i,
            );
            assembler.insert(p, FieldKind::RelativeHumidity, 50.0, i);
            assembler.insert(p, FieldKind::UWind, 3.0, i);
            assembler.insert(p, FieldKind::VWind, 4.0, i);
        }
        for (field, value) in [
            (SurfaceFieldKind::Pressure, 100000.0),
            (SurfaceFieldKind::Height, 10.0),
            (SurfaceFieldKind::Temperature, 290.0),
            (SurfaceFieldKind::Dewpoint, 290.0 + excess),
            (SurfaceFieldKind::UWind, 3.0),
            (SurfaceFieldKind::VWind, 4.0),
        ] {
            assembler.insert_surface(field, value);
        }
        let point = assembler
            .assemble(
                GridPoint {
                    index: 0,
                    latitude: 35.0,
                    longitude: -97.0,
                },
                Some(-9999.0),
            )
            .unwrap();
        assert_eq!(point.surface_merged, accepted, "surface excess {excess}");
        if accepted {
            assert_eq!(
                point.matrix[2 * point.level_count],
                point.matrix[3 * point.level_count]
            );
        }
    }
}
