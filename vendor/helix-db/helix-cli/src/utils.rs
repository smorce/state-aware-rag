use crate::errors::CliError;
use color_eyre::owo_colors::OwoColorize;
use eyre::Result;
use std::io::IsTerminal;

pub fn command_exists(command: &str) -> bool {
    std::process::Command::new("which")
        .arg(command)
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
}

pub fn print_newline() {
    println!();
}

pub fn print_lines(lines: &[&str]) {
    for line in lines {
        println!("  {line}");
    }
}

pub fn print_instructions(title: &str, steps: &[&str]) {
    if !crate::output::Verbosity::current().show_normal() {
        return;
    }
    print_newline();
    println!("{}", title.bold());
    for (i, step) in steps.iter().enumerate() {
        println!("  {}. {step}", (i + 1).to_string().bright_white().bold());
    }
}

pub fn print_header(title: &str) {
    println!("{}", title.bold().underline());
}

pub fn print_field(key: &str, value: &str) {
    println!("  {}: {value}", key.bright_white().bold());
}

pub fn print_error(message: &str) {
    let error = CliError::new(message);
    eprint!("{}", error.render());
}

pub fn print_error_with_hint(message: &str, hint: &str) {
    let error = CliError::new(message).with_hint(hint);
    eprint!("{}", error.render());
}

pub fn print_warning(message: &str) {
    let warning = CliError::warning(message);
    eprint!("{}", warning.render());
}

pub fn print_confirm(message: &str) -> Result<bool> {
    if !std::io::stdin().is_terminal() {
        return Ok(false);
    }

    crate::prompts::confirm(message)
}

pub fn print_prompt(message: &str) -> std::io::Result<String> {
    use std::io::{self, Write};
    print!("{} ", message.yellow().bold());
    io::stdout().flush()?;
    let mut input = String::new();
    io::stdin().read_line(&mut input)?;
    Ok(input)
}

pub fn add_env_var_to_file(path: &std::path::Path, key: &str, value: &str) -> Result<()> {
    let mut content = std::fs::read_to_string(path).unwrap_or_default();
    let replacement = format!("{key}={value}");
    let mut replaced = false;

    let lines: Vec<String> = content
        .lines()
        .map(|line| {
            if line.trim_start().starts_with(&format!("{key}=")) {
                replaced = true;
                replacement.clone()
            } else {
                line.to_string()
            }
        })
        .collect();

    content = lines.join("\n");
    if !replaced {
        if !content.is_empty() && !content.ends_with('\n') {
            content.push('\n');
        }
        content.push_str(&replacement);
    }
    if !content.ends_with('\n') {
        content.push('\n');
    }

    std::fs::write(path, content)?;
    Ok(())
}
