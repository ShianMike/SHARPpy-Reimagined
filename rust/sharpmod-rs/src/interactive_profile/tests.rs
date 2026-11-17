use super::*;
#[test]
fn original_values_keep_masked_columns_and_heterogeneous_storm_missing_values() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py|->PyResult<()> {
        let scope=PyDict::new(py);
        py.run(&std::ffi::CString::new(r#"
import numpy as np
from types import SimpleNamespace
profile=SimpleNamespace(tmpc=np.ma.array([0.,1.],mask=[0,1]),dwpc=np.array([np.nan,np.inf]),u=np.array([-9999.,0.]),v=np.array([0.,np.nan]),srwind=(np.ma.masked,10.,20.,None))
"#).unwrap(),Some(&scope),Some(&scope))?;
        scope.set_item("values",sounding_original_values(py,&scope.get_item("profile")?.unwrap())?)?;
        py.run(&std::ffi::CString::new("assert values == dict(tmpc=[0.,None],dwpc=[None,None],u=[-9999.,0.],v=[0.,None],storm=[None,10.,20.,None])").unwrap(),Some(&scope),Some(&scope))?;Ok(())
    }).unwrap();
}
use pyo3::ffi::c_str;
#[test]
fn edits_copy_each_column_once_and_reject_a_partial_transaction() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py|->PyResult<()> {
        let fixture=PyModule::from_code(py,c_str!(r#"
from types import SimpleNamespace
import numpy as np
class Array(np.ndarray):
    copies=0
    def copy(self):
        Array.copies += 1
        return super().copy()
p=SimpleNamespace(pres=np.array([1000.,900.,800.]),tmpc=np.array([20.,10.,0.]).view(Array),dwpc=np.array([10.,0.,-10.]))
class Collection:
    def __init__(self): self.calls=0
    def getHighlightedProf(self): return p
    def modify(self,index,**values): self.calls+=1; p.__dict__.update(values)
    def modifyStormMotion(self,*args): self.calls+=1
c=Collection()
edits=[{'index':0,'values':{'tmpc':21.}}, {'index':1,'values':{'tmpc':11.}}]
bad=[{'index':0,'values':{'tmpc':30.}}, {'index':-1,'values':{'u':1.}}]
"#),c_str!("edit_fixture.py"),c_str!("edit_fixture"))?;
        apply_sounding_edits(py,&fixture.getattr("c")?,fixture.getattr("edits")?.cast()?)?;
        assert_eq!(fixture.getattr("Array")?.getattr("copies")?.extract::<usize>()?,1);
        assert!(apply_sounding_edits(py,&fixture.getattr("c")?,fixture.getattr("bad")?.cast()?).is_err());
        assert_eq!(fixture.getattr("c")?.getattr("calls")?.extract::<usize>()?,1);
        Ok(())
    }).unwrap();
}
