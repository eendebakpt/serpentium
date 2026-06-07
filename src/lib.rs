//! serpentium native backends -- the single compiled extension module
//! `serpentium._native`, shared by the `randium` and `htmlium` subpackages.
//!
//! Maturin builds one extension module per wheel, so both native accelerators live
//! here behind one `#[pymodule]`, split across source files:
//!   * `randium` -- the Mersenne-Twister core + bounded-integer generators + shuffle
//!     (the `RandiumState` class), a port of CPython `Modules/_randommodule.c`.
//!   * `html`    -- the `html.escape` / `html.unescape` accelerator, with in-place
//!     vectorcall patching (see `html.rs`); its entity tables are generated into
//!     `html_entities.rs` by `tools/generate_html_entities.py`.

use pyo3::ffi;
use pyo3::prelude::*;

mod html;
mod html_entities;
mod randium;

use randium::RandiumState;

/// Set a Python function object's vectorcall slot in place.
///
/// Writes the struct fields directly instead of calling `PyFunction_SetVectorcall`,
/// avoiding a dependency on that symbol (and the Windows link/abi quirks around it).
/// Zeroing `func_version` invalidates the interpreter's per-function specialization,
/// exactly as CPython's own setter does.
///
/// Safety: `func` must be a live `PyFunctionObject`; caller holds the GIL.
pub(crate) unsafe fn set_function_vectorcall(
    func: *mut ffi::PyObject,
    vectorcall: Option<ffi::vectorcallfunc>,
) {
    let f = func as *mut ffi::PyFunctionObject;
    (*f).func_version = 0;
    (*f).vectorcall = vectorcall;
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RandiumState>()?;
    randium::register(m)?;
    html::register(m)?;
    m.add(
        "__doc__",
        "Native Rust backends for serpentium: the randium Mersenne-Twister core \
         and the htmlium html.escape/html.unescape accelerator.",
    )?;
    Ok(())
}
