use pyo3_stub_gen::Result;

fn main() -> Result<()> {
    let stub = endoreg_rust_backend::stub_info()?;
    stub.generate()?;
    Ok(())
}
