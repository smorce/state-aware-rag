use crate::InitTarget;
use crate::config::{
    DEFAULT_QUERY_AUTH_ENV, DEFAULT_QUERY_AUTH_HEADER, EnterpriseInstanceConfig, HelixConfig,
    LocalInstanceConfig, LocalStorageMode,
};
use crate::output::Operation;
use crate::prompts;
use crate::utils::{command_exists, print_instructions};
use eyre::Result;
use std::env;
use std::fs;
use std::io::Write;
use std::path::Path;

pub async fn run(
    path: Option<String>,
    target: Option<InitTarget>,
    skills: Option<bool>,
) -> Result<()> {
    let project_dir = match path {
        Some(path) => std::path::PathBuf::from(path),
        None => env::current_dir()?,
    };
    let project_name = project_dir
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("helix-project")
        .to_string();
    let config_path = project_dir.join("helix.toml");

    if config_path.exists() {
        return Err(eyre::eyre!(
            "helix.toml already exists in {}",
            project_dir.display()
        ));
    }

    fs::create_dir_all(&project_dir)?;
    fs::create_dir_all(project_dir.join(".helix"))?;

    let op = Operation::new("Initializing", &project_name);
    let mut config = HelixConfig::default_config(&project_name);

    let target = match target {
        Some(target) => target,
        None if prompts::is_interactive() => prompts::select_init_target()?,
        None => InitTarget::Local {
            name: "dev".to_string(),
            port: crate::config::DEFAULT_LOCAL_PORT,
            disk: false,
            skills: false,
            no_skills: false,
        },
    };

    // `--skills`/`--no-skills` may be given after the subcommand
    // (`helix init local --no-skills`); the subcommand-level flag wins over the
    // top-level one when both are present.
    let skills = target.skills_override().or(skills);

    let next_steps = match target {
        InitTarget::Local {
            name, port, disk, ..
        } => {
            // Surface a missing/stopped container runtime before we write any files,
            // so the user can react before the project is scaffolded.
            crate::setup::warn_if_container_runtime_unavailable();
            let instance_name = name.clone();
            config.local.clear();
            config.local.insert(
                name,
                LocalInstanceConfig {
                    port,
                    storage: LocalStorageMode::from_disk_flag(disk),
                    ..LocalInstanceConfig::default()
                },
            );
            write_example_request(&project_dir)?;
            local_next_steps(&instance_name)
        }
        InitTarget::Enterprise {
            name,
            cluster_id,
            gateway_url,
            ..
        } => {
            let instance_name = name.clone();
            let target = crate::commands::config::resolve_enterprise_target(
                cluster_id,
                gateway_url,
                None,
                None,
            )
            .await?;
            config.local.clear();
            config.enterprise.insert(
                name,
                EnterpriseInstanceConfig {
                    cluster_id: target.cluster_id,
                    workspace_id: target.workspace_id,
                    project_id: target.project_id,
                    gateway_url: target.gateway_url,
                    query_auth_header: DEFAULT_QUERY_AUTH_HEADER.to_string(),
                    query_auth_env: DEFAULT_QUERY_AUTH_ENV.to_string(),
                    availability_mode: None,
                    gateway_node_type: None,
                    db_node_type: None,
                    min_instances: 1,
                    max_instances: 1,
                    db_config: Default::default(),
                },
            );
            enterprise_next_steps(&instance_name)
        }
    };

    config.save_to_file(&config_path)?;
    append_gitignore(&project_dir)?;
    op.success();

    maybe_install_tooling(&project_dir, skills);

    let next_step_refs: Vec<&str> = next_steps.iter().map(String::as_str).collect();
    print_instructions("Next steps:", &next_step_refs);

    Ok(())
}

/// Install the Helix agent skills + docs MCP via `npx`, like `helix chef` does.
///
/// `skills` is `Some(true)`/`Some(false)` when set via `--skills`/`--no-skills`;
/// when `None`, prompt in an interactive terminal (default yes) and skip otherwise.
/// Tooling is a convenience, so failures degrade to a warning rather than aborting
/// the freshly scaffolded project.
fn maybe_install_tooling(project_dir: &Path, skills: Option<bool>) {
    let install = match skills {
        Some(value) => value,
        None => {
            prompts::is_interactive()
                && prompts::confirm("Install Helix skills and docs MCP?").unwrap_or(false)
        }
    };
    if !install {
        return;
    }

    if !command_exists("npx") {
        crate::output::warning(
            "npx not found; skipping Helix skills + docs MCP install. Install Node.js/npm, \
             then run 'npx skills add HelixDB/skills'.",
        );
        return;
    }

    if let Err(err) = crate::setup::install_skills(project_dir, true, true) {
        crate::output::warning(&format!("Skipping Helix skills install: {err}"));
    }
    if let Err(err) = crate::setup::install_mcp(project_dir, true, true) {
        crate::output::warning(&format!("Skipping Helix docs MCP install: {err}"));
    }
}

fn local_next_steps(instance_name: &str) -> Vec<String> {
    vec![
        format!(
            "Run 'helix start {instance_name}' to start local Helix Enterprise dev in the background"
        ),
        format!("Run 'helix query {instance_name} --file examples/request.json'"),
        format!(
            "Or query in TypeScript: helix query {instance_name} -e 'readBatch().varAs(\"c\", g().nWithLabel(\"User\").count()).returning([\"c\"])'"
        ),
    ]
}

fn enterprise_next_steps(instance_name: &str) -> Vec<String> {
    vec![
        format!("Run 'helix sync {instance_name}' to refresh Enterprise Cloud metadata"),
        format!("Run 'helix query {instance_name} --file <request.json>'"),
    ]
}

fn write_example_request(project_dir: &Path) -> Result<()> {
    let examples_dir = project_dir.join("examples");
    fs::create_dir_all(&examples_dir)?;
    let request_path = examples_dir.join("request.json");
    if request_path.exists() {
        return Ok(());
    }

    let request = serde_json::json!({
        "request_type": "read",
        "query": {
            "queries": [{
                "Query": {
                    "name": "node_count",
                    "steps": [
                        {"NWhere": {"Eq": ["$label", {"String": "User"}]}},
                        "Count"
                    ],
                    "condition": null
                }
            }],
            "returns": ["node_count"]
        },
        "parameters": {}
    });

    fs::write(&request_path, serde_json::to_string_pretty(&request)?)?;
    Ok(())
}

fn append_gitignore(project_dir: &Path) -> Result<()> {
    let gitignore_path = project_dir.join(".gitignore");
    let existing = fs::read_to_string(&gitignore_path).unwrap_or_default();
    let entries = [".helix/", "target/", "*.log"];
    let missing: Vec<&str> = entries
        .into_iter()
        .filter(|entry| !existing.lines().any(|line| line.trim() == *entry))
        .collect();

    if missing.is_empty() {
        return Ok(());
    }

    let mut file = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&gitignore_path)?;
    if !existing.is_empty() && !existing.ends_with('\n') {
        writeln!(file)?;
    }
    for entry in missing {
        writeln!(file, "{entry}")?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn example_request_starts_with_source_step() {
        let dir = std::env::temp_dir().join(format!(
            "helix-init-test-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        write_example_request(&dir).unwrap();
        let request: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(dir.join("examples/request.json")).unwrap(),
        )
        .unwrap();
        let steps = &request["query"]["queries"][0]["Query"]["steps"];

        assert!(steps[0].get("NWhere").is_some());
        assert_eq!(steps[1], "Count");
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn local_next_steps_use_instance_name() {
        let steps = local_next_steps("qa");

        assert!(steps[0].contains("helix start qa"));
        assert!(steps[2].contains("helix query qa"));
    }

    #[test]
    fn enterprise_next_steps_use_instance_name() {
        let steps = enterprise_next_steps("production");

        assert!(steps[0].contains("helix sync production"));
        assert!(steps[1].contains("helix query production"));
    }
}
