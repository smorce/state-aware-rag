use crate::commands::auth::require_auth;
use crate::config::WorkspaceConfig;
use crate::enterprise_cloud::{
    CliClusterIndex, CliClusterIndexes, CliEnterpriseCluster, cloud_base_url,
    fetch_indexes_for_cluster, fetch_projects, fetch_workspaces, find_project_by_id,
    find_project_by_name, find_workspace_by_id, find_workspace_by_slug, list_clusters_for_context,
    resolve_enterprise_cluster,
};
use crate::project::ProjectContext;
use crate::prompts;
use crate::{
    ClusterConfigAction, ConfigAction, ConfigOutputFormat, ProjectConfigAction,
    WorkspaceConfigAction,
};
use color_eyre::owo_colors::OwoColorize;
use eyre::{Result, WrapErr, eyre};
use serde::Serialize;

pub async fn run(action: Option<ConfigAction>) -> Result<()> {
    match action {
        Some(ConfigAction::Workspace { action }) => run_workspace(Some(action)).await,
        Some(ConfigAction::Project { action }) => run_project(Some(action)).await,
        Some(ConfigAction::Cluster { action }) => run_cluster(Some(action)).await,
        None if prompts::is_interactive() => interactive_config().await,
        None => Err(eyre!(
            "Specify a config command: 'helix workspace', 'helix project', or 'helix cluster'"
        )),
    }
}

pub async fn run_workspace(action: Option<WorkspaceConfigAction>) -> Result<()> {
    match action {
        Some(WorkspaceConfigAction::List { format }) => workspace_list(format).await,
        Some(WorkspaceConfigAction::Show { format }) => workspace_show(format).await,
        Some(WorkspaceConfigAction::Switch { workspace, id }) => {
            workspace_switch(&workspace, id).await
        }
        None if prompts::is_interactive() => workspace_select().await,
        None => Err(eyre!(
            "Specify a workspace command: 'helix workspace list', 'helix workspace show', or 'helix workspace switch <workspace>'"
        )),
    }
}

pub async fn run_project(action: Option<ProjectConfigAction>) -> Result<()> {
    match action {
        Some(ProjectConfigAction::List {
            workspace_id,
            format,
        }) => project_list(workspace_id, format).await,
        Some(ProjectConfigAction::Show { format }) => project_show(format).await,
        Some(ProjectConfigAction::Switch { project, id }) => project_switch(&project, id).await,
        None if prompts::is_interactive() => project_select().await,
        None => Err(eyre!(
            "Specify a project command: 'helix project list', 'helix project show', or 'helix project switch <project>'"
        )),
    }
}

pub async fn run_cluster(action: Option<ClusterConfigAction>) -> Result<()> {
    match action {
        Some(ClusterConfigAction::List {
            workspace_id,
            project_id,
            format,
        }) => cluster_list(workspace_id, project_id, format).await,
        Some(ClusterConfigAction::Indexes { cluster_id, format }) => {
            list_indexes_for_cluster(cluster_id, format).await
        }
        None if prompts::is_interactive() => cluster_select().await,
        None => Err(eyre!(
            "Specify a cluster command: 'helix cluster list' or 'helix cluster indexes'"
        )),
    }
}

async fn interactive_config() -> Result<()> {
    #[derive(Clone, Copy, PartialEq, Eq)]
    enum ConfigTarget {
        Workspace,
        Project,
        Cluster,
    }

    let target = cliclack::select("What would you like to configure?")
        .item(
            ConfigTarget::Workspace,
            "Workspace",
            "Choose active Enterprise Cloud workspace",
        )
        .item(
            ConfigTarget::Project,
            "Project",
            "Link this project to Enterprise Cloud",
        )
        .item(
            ConfigTarget::Cluster,
            "Cluster",
            "Inspect Enterprise Cloud clusters",
        )
        .interact()?;

    match target {
        ConfigTarget::Workspace => workspace_select().await,
        ConfigTarget::Project => project_select().await,
        ConfigTarget::Cluster => cluster_select().await,
    }
}

fn print_json<T: Serialize>(value: &T) -> Result<()> {
    println!("{}", serde_json::to_string_pretty(value)?);
    Ok(())
}

async fn workspace_list(format: ConfigOutputFormat) -> Result<()> {
    let credentials = require_auth().await?;
    let client = reqwest::Client::new();
    let workspaces =
        fetch_workspaces(&client, &cloud_base_url(), &credentials.helix_admin_key).await?;
    if format == ConfigOutputFormat::Json {
        return print_json(&workspaces);
    }
    println!("{}", "Workspaces".bold());
    for workspace in workspaces {
        println!("  {} ({})", workspace.name, workspace.url_slug);
    }
    Ok(())
}

async fn workspace_show(format: ConfigOutputFormat) -> Result<()> {
    let config = WorkspaceConfig::load()?;
    if format == ConfigOutputFormat::Json {
        return print_json(&config);
    }
    match config.workspace_id {
        Some(id) => println!("Selected workspace: {id}"),
        None => println!("No workspace selected"),
    }
    Ok(())
}

async fn workspace_switch(selector: &str, use_id: bool) -> Result<()> {
    let credentials = require_auth().await?;
    let client = reqwest::Client::new();
    let workspaces =
        fetch_workspaces(&client, &cloud_base_url(), &credentials.helix_admin_key).await?;
    let selected = if use_id {
        find_workspace_by_id(&workspaces, selector)
    } else {
        find_workspace_by_slug(&workspaces, selector)
    }
    .ok_or_else(|| eyre!("Workspace '{selector}' was not found"))?;

    let config = WorkspaceConfig {
        workspace_id: Some(selected.id.clone()),
    };
    config.save()?;
    crate::output::success(&format!("Selected workspace '{}'", selected.name));
    Ok(())
}

async fn workspace_select() -> Result<()> {
    let credentials = require_auth().await?;
    let client = reqwest::Client::new();
    let workspaces =
        fetch_workspaces(&client, &cloud_base_url(), &credentials.helix_admin_key).await?;
    let items: Vec<(String, String, String)> = workspaces
        .iter()
        .map(|workspace| {
            (
                workspace.id.clone(),
                workspace.name.clone(),
                workspace.url_slug.clone(),
            )
        })
        .collect();
    let selected_id = prompts::select_workspace(&items)?;
    let selected = workspaces
        .iter()
        .find(|workspace| workspace.id == selected_id)
        .ok_or_else(|| eyre!("Selected workspace was not found"))?;
    WorkspaceConfig {
        workspace_id: Some(selected.id.clone()),
    }
    .save()?;
    crate::output::success(&format!("Selected workspace '{}'", selected.name));
    Ok(())
}

async fn project_list(workspace_id: Option<String>, format: ConfigOutputFormat) -> Result<()> {
    let credentials = require_auth().await?;
    let client = reqwest::Client::new();
    let workspace_id = workspace_id
        .or_else(|| {
            WorkspaceConfig::load()
                .ok()
                .and_then(|config| config.workspace_id)
        })
        .ok_or_else(|| {
            eyre!("No workspace selected. Run 'helix config workspace switch <workspace>'.")
        })?;
    let projects = fetch_projects(
        &client,
        &cloud_base_url(),
        &credentials.helix_admin_key,
        &workspace_id,
    )
    .await?;
    if format == ConfigOutputFormat::Json {
        return print_json(&projects);
    }
    println!("{}", "Projects".bold());
    for project in projects {
        println!("  {} ({})", project.name, project.id);
    }
    Ok(())
}

async fn project_show(format: ConfigOutputFormat) -> Result<()> {
    let project = ProjectContext::find_and_load(None)?;
    if format == ConfigOutputFormat::Json {
        return print_json(&project.config.project);
    }
    println!("Project: {}", project.config.project.name);
    if let Some(id) = &project.config.project.id {
        println!("ID: {id}");
    }
    if let Some(workspace_id) = &project.config.project.workspace_id {
        println!("Workspace ID: {workspace_id}");
    }
    Ok(())
}

async fn project_switch(selector: &str, use_id: bool) -> Result<()> {
    let credentials = require_auth().await?;
    let client = reqwest::Client::new();
    let workspace_id = WorkspaceConfig::load()?.workspace_id.ok_or_else(|| {
        eyre!("No workspace selected. Run 'helix config workspace switch <workspace>'.")
    })?;
    let projects = fetch_projects(
        &client,
        &cloud_base_url(),
        &credentials.helix_admin_key,
        &workspace_id,
    )
    .await?;
    let selected = if use_id {
        find_project_by_id(&projects, selector)
    } else {
        find_project_by_name(&projects, selector)
    }
    .ok_or_else(|| eyre!("Project '{selector}' was not found"))?;

    let mut project = ProjectContext::find_and_load(None)?;
    project.config.project.id = Some(selected.id.clone());
    project.config.project.workspace_id = Some(workspace_id);
    project.config.project.name = selected.name.clone();
    project
        .config
        .save_to_file(&project.root.join("helix.toml"))?;
    crate::output::success(&format!("Linked project '{}'", selected.name));
    Ok(())
}

async fn project_select() -> Result<()> {
    let credentials = require_auth().await?;
    let client = reqwest::Client::new();
    let workspace_id = WorkspaceConfig::load()?.workspace_id.ok_or_else(|| {
        eyre!(
            "No workspace selected. Run 'helix workspace' or 'helix workspace switch <workspace>'."
        )
    })?;
    let projects = fetch_projects(
        &client,
        &cloud_base_url(),
        &credentials.helix_admin_key,
        &workspace_id,
    )
    .await?;
    let items: Vec<(String, String)> = projects
        .iter()
        .map(|project| (project.id.clone(), project.name.clone()))
        .collect();
    let selected_id = prompts::select_project(&items)?;
    let selected = projects
        .iter()
        .find(|project| project.id == selected_id)
        .ok_or_else(|| eyre!("Selected project was not found"))?;

    let mut project = ProjectContext::find_and_load(None)?;
    project.config.project.id = Some(selected.id.clone());
    project.config.project.workspace_id = Some(workspace_id);
    project.config.project.name = selected.name.clone();
    project
        .config
        .save_to_file(&project.root.join("helix.toml"))?;
    crate::output::success(&format!("Linked project '{}'", selected.name));
    Ok(())
}

async fn cluster_list(
    workspace_id: Option<String>,
    project_id: Option<String>,
    format: ConfigOutputFormat,
) -> Result<()> {
    let credentials = require_auth().await?;
    let client = reqwest::Client::new();
    let workspace_id = workspace_id.or_else(|| {
        WorkspaceConfig::load()
            .ok()
            .and_then(|config| config.workspace_id)
    });
    let clusters = list_clusters_for_context(
        &client,
        &cloud_base_url(),
        &credentials.helix_admin_key,
        project_id.as_deref(),
        workspace_id.as_deref(),
    )
    .await?;

    if format == ConfigOutputFormat::Json {
        return print_json(&clusters);
    }
    print_enterprise_clusters(&clusters);
    Ok(())
}

async fn list_indexes_for_cluster(
    cluster_id: Option<String>,
    format: ConfigOutputFormat,
) -> Result<()> {
    let cluster_id = resolve_cluster_id_for_indexes(cluster_id)?;
    let credentials = require_auth().await?;
    let client = reqwest::Client::new();
    let indexes = fetch_indexes_for_cluster(
        &client,
        &cloud_base_url(),
        &credentials.helix_admin_key,
        cluster_id.as_str(),
    )
    .await?;

    if format == ConfigOutputFormat::Json {
        return print_json(&indexes);
    }

    print_cluster_indexes(&cluster_id, &indexes);
    Ok(())
}

fn resolve_cluster_id_for_indexes(cluster_id: Option<String>) -> Result<String> {
    if let Some(cluster_id) = cluster_id {
        let cluster_id = cluster_id.trim();
        if !cluster_id.is_empty() {
            return Ok(cluster_id.to_string());
        }
    }

    let project = ProjectContext::find_and_load(None).wrap_err(
        "Provide --cluster-id, or run inside a Helix project with an Enterprise instance.",
    )?;

    let mut enterprise_instances = project
        .config
        .enterprise
        .keys()
        .map(|name| (name.clone(), "Enterprise".to_string()))
        .collect::<Vec<_>>();
    enterprise_instances.sort_by(|a, b| a.0.cmp(&b.0));

    let instance_name = match enterprise_instances.len() {
        0 => return Err(eyre!("No Enterprise instances found in helix.toml")),
        1 => enterprise_instances[0].0.clone(),
        _ if prompts::is_interactive() => prompts::select_instance(
            &enterprise_instances,
            "List indexes for which Enterprise instance?",
        )?,
        _ => {
            let available = enterprise_instances
                .into_iter()
                .map(|(name, _)| name)
                .collect::<Vec<_>>()
                .join(", ");
            return Err(eyre!(
                "No Enterprise instance specified. Available Enterprise instances: {available}. Pass --cluster-id to select one."
            ));
        }
    };

    project
        .config
        .enterprise
        .get(&instance_name)
        .map(|config| config.cluster_id.clone())
        .ok_or_else(|| eyre!("Enterprise instance '{instance_name}' was not found"))
}

fn print_cluster_indexes(cluster_id: &str, indexes: &CliClusterIndexes) {
    println!("{}", "Cluster indexes".bold());
    println!("  Cluster: {cluster_id}");
    print_index_group("Vector indexes", &indexes.vector_indexes);
    print_index_group("Equality indexes", &indexes.equality_indexes);
    print_index_group("Range indexes", &indexes.range_indexes);
}

fn print_index_group(title: &str, indexes: &[CliClusterIndex]) {
    println!("  {title}:");
    if indexes.is_empty() {
        println!("    (none)");
        return;
    }

    for index in indexes {
        let name = if index.index_name.trim().is_empty() {
            "<unnamed>"
        } else {
            index.index_name.as_str()
        };
        match index.index_type.as_deref() {
            Some(index_type) if !index_type.trim().is_empty() => {
                println!("    {name} ({index_type})");
            }
            _ => println!("    {name}"),
        }
    }
}

fn print_enterprise_clusters(clusters: &[CliEnterpriseCluster]) {
    println!("{}", "Enterprise clusters".bold());
    for cluster in clusters {
        println!("  {} ({})", cluster.name, cluster.cluster_id);
        if let Some(gateway_url) = &cluster.gateway_url {
            println!("    gateway: {gateway_url}");
        }
    }
}

async fn cluster_select() -> Result<()> {
    let credentials = require_auth().await?;
    let client = reqwest::Client::new();
    let project_context = ProjectContext::find_and_load(None).ok();
    let project_id = project_context
        .as_ref()
        .and_then(|project| project.config.project.id.clone());
    let workspace_id = if project_id.is_some() {
        None
    } else {
        Some(WorkspaceConfig::load()?.workspace_id.ok_or_else(|| {
            eyre!("No workspace selected. Run 'helix workspace' or 'helix workspace switch <workspace>'.")
        })?)
    };
    let clusters = list_clusters_for_context(
        &client,
        &cloud_base_url(),
        &credentials.helix_admin_key,
        project_id.as_deref(),
        workspace_id.as_deref(),
    )
    .await?;

    let items: Vec<(String, String, String)> = clusters
        .iter()
        .map(|cluster| {
            let hint = cluster
                .project_name
                .as_deref()
                .unwrap_or("Enterprise cluster")
                .to_string();
            (cluster.cluster_id.clone(), cluster.name.clone(), hint)
        })
        .collect();
    let selected_id = prompts::select_cluster(&items)?;
    let selected = clusters
        .iter()
        .find(|cluster| cluster.cluster_id == selected_id)
        .ok_or_else(|| eyre!("Selected Enterprise cluster was not found"))?;

    println!("{}", "Enterprise cluster".bold());
    println!("  Name: {}", selected.name);
    println!("  ID: {}", selected.cluster_id);
    if let Some(project_name) = &selected.project_name {
        println!("  Project: {project_name}");
    }
    if let Some(gateway_url) = &selected.gateway_url {
        println!("  Gateway: {gateway_url}");
    }
    Ok(())
}

/// An Enterprise cluster resolved into the fields needed to write an
/// `[enterprise.<name>]` block in helix.toml.
pub struct EnterpriseTarget {
    pub cluster_id: String,
    pub project_id: Option<String>,
    pub workspace_id: Option<String>,
    pub gateway_url: Option<String>,
}

/// Resolve the cluster (and its owning project/workspace + gateway URL) for
/// `helix init cloud` / `helix add cloud`.
///
/// When `cluster_id` is `None`, the user picks one from the cluster list (only in
/// an interactive terminal). When it is `Some`, the cloud API is queried to fill in
/// the owning project/workspace. In both cases an explicit `gateway_url_override`
/// wins over the cluster's own gateway URL.
pub async fn resolve_enterprise_target(
    cluster_id: Option<String>,
    gateway_url_override: Option<String>,
    project_ctx: Option<String>,
    workspace_ctx: Option<String>,
) -> Result<EnterpriseTarget> {
    let credentials = require_auth().await?;
    let client = reqwest::Client::new();
    let base_url = cloud_base_url();
    let api_key = &credentials.helix_admin_key;

    match cluster_id {
        Some(cluster_id) => {
            let resolved = resolve_enterprise_cluster(
                &client,
                &base_url,
                api_key,
                &cluster_id,
                project_ctx.as_deref(),
                workspace_ctx.as_deref(),
            )
            .await?;
            Ok(EnterpriseTarget {
                cluster_id,
                project_id: Some(resolved.project_id),
                workspace_id: resolved.workspace_id,
                gateway_url: gateway_url_override.or(resolved.cluster.gateway_url),
            })
        }
        None => {
            if !prompts::is_interactive() {
                return Err(eyre!(
                    "Provide --cluster-id, or run interactively to pick a cluster from the list."
                ));
            }
            let workspace_id = workspace_ctx.or_else(|| {
                WorkspaceConfig::load()
                    .ok()
                    .and_then(|config| config.workspace_id)
            });
            let clusters = list_clusters_for_context(
                &client,
                &base_url,
                api_key,
                project_ctx.as_deref(),
                workspace_id.as_deref(),
            )
            .await?;
            let items: Vec<(String, String, String)> = clusters
                .iter()
                .map(|cluster| {
                    let hint = cluster
                        .project_name
                        .as_deref()
                        .unwrap_or("Enterprise cluster")
                        .to_string();
                    (cluster.cluster_id.clone(), cluster.name.clone(), hint)
                })
                .collect();
            let selected_id = prompts::select_cluster(&items)?;
            let cluster = clusters
                .into_iter()
                .find(|cluster| cluster.cluster_id == selected_id)
                .ok_or_else(|| eyre!("Selected Enterprise cluster was not found"))?;
            Ok(EnterpriseTarget {
                cluster_id: cluster.cluster_id,
                project_id: cluster.project_id.or(project_ctx),
                workspace_id,
                gateway_url: gateway_url_override.or(cluster.gateway_url),
            })
        }
    }
}
