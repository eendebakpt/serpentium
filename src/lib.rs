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

use pyo3::prelude::*;

mod html;
mod html_entities;
mod randium;

use randium::RandiumState;

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
