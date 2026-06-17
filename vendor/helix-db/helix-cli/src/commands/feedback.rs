use eyre::Result;

pub async fn run(message: Option<String>) -> Result<()> {
    let message = message.unwrap_or_else(|| "Helix CLI feedback".to_string());
    let url = format!(
        "https://github.com/helixdb/helix-db/issues/new?title={}&body={}",
        urlencoding::encode("feedback: Helix CLI"),
        urlencoding::encode(&message)
    );
    open::that(url)?;
    crate::output::success("Opened feedback issue in your browser");
    Ok(())
}
