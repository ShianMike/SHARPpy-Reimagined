use super::*;
#[test]
fn zarr_geometry_rejects_bad_chunks_and_handles_partial_edges() {
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let meta = PyDict::new(py);
        meta.set_item("shape", [5, 7])?;
        meta.set_item("chunks", [3, 4])?;
        assert_eq!(geometry(&meta, 4, 6)?, ([3, 4], [2, 3], [1, 2]));
        assert!(geometry(&meta, 5, 0).is_err());
        meta.set_item("chunks", [0, 4])?;
        assert!(geometry(&meta, 0, 0).is_err());
        Ok(())
    })
    .unwrap();
}
