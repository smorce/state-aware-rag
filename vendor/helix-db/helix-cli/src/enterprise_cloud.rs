use eyre::{Result, eyre};
use reqwest::Client;
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use std::collections::BTreeMap;
use std::sync::LazyLock;

const DEFAULT_CLOUD_AUTHORITY: &str = "cloud.helix-db.com";

pub static CLOUD_AUTHORITY: LazyLock<String> = LazyLock::new(|| {
    std::env::var("CLOUD_AUTHORITY").unwrap_or_else(|_| DEFAULT_CLOUD_AUTHORITY.to_string())
});

pub fn cloud_base_url() -> String {
    let authority = CLOUD_AUTHORITY.as_str();
    if authority.starts_with("http://") || authority.starts_with("https://") {
        authority.to_string()
    } else if authority.starts_with("localhost") || authority.starts_with("127.0.0.1") {
        format!("http://{authority}")
    } else {
        format!("https://{authority}")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CliWorkspace {
    pub id: String,
    pub name: String,
    pub url_slug: String,
    #[serde(default = "default_workspace_type")]
    pub workspace_type: String,
}

fn default_workspace_type() -> String {
    "organization".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CliProject {
    pub id: String,
    pub name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CliProjectDetails {
    pub id: String,
    pub name: String,
    pub workspace_id: String,
    pub workspace_name: String,
    pub workspace_slug: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CliProjectClusters {
    pub project_id: String,
    pub project_name: String,
    #[serde(default)]
    pub enterprise: Vec<CliEnterpriseCluster>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CliWorkspaceClusters {
    #[serde(default)]
    pub enterprise: Vec<CliEnterpriseCluster>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CliClusterIndex {
    #[serde(default, rename = "name", alias = "index_name")]
    pub index_name: String,
    #[serde(default, rename = "type", alias = "index_type")]
    pub index_type: Option<String>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CliClusterIndexes {
    #[serde(default)]
    pub vector_indexes: Vec<CliClusterIndex>,
    #[serde(default)]
    pub equality_indexes: Vec<CliClusterIndex>,
    #[serde(default)]
    pub range_indexes: Vec<CliClusterIndex>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CliEnterpriseCluster {
    pub cluster_id: String,
    #[serde(alias = "cluster_name")]
    pub name: String,
    #[serde(default)]
    pub project_id: Option<String>,
    #[serde(default)]
    pub project_name: Option<String>,
    #[serde(default)]
    pub availability_mode: Option<String>,
    #[serde(default)]
    pub gateway_node_type: Option<String>,
    #[serde(default)]
    pub db_node_type: Option<String>,
    #[serde(default)]
    pub gateway_url: Option<String>,
    #[serde(default)]
    pub query_auth_header: Option<String>,
    #[serde(default)]
    pub query_auth_env: Option<String>,
    #[serde(default)]
    pub min_gateway_count: Option<u64>,
    #[serde(default)]
    pub max_gateway_count: Option<u64>,
    #[serde(default)]
    pub min_hyperscale_count: Option<u64>,
    #[serde(default)]
    pub max_hyperscale_count: Option<u64>,
    #[serde(default)]
    pub gateway_count: Option<u64>,
    #[serde(default)]
    pub hyperscale_count: Option<u64>,
    #[serde(default)]
    pub min_instances: Option<u64>,
    #[serde(default)]
    pub max_instances: Option<u64>,
}

impl CliEnterpriseCluster {
    pub fn resolved_gateway_min_count(&self) -> Option<u64> {
        self.min_gateway_count
            .or(self.gateway_count)
            .or(self.max_gateway_count)
            .or(self.min_instances)
    }

    pub fn resolved_gateway_max_count(&self) -> Option<u64> {
        self.max_gateway_count
            .or(self.gateway_count)
            .or(self.min_gateway_count)
            .or(self.min_instances)
    }

    pub fn resolved_hyperscale_min_count(&self) -> Option<u64> {
        self.min_hyperscale_count
            .or(self.hyperscale_count)
            .or(self.max_hyperscale_count)
            .or(self.max_instances)
    }

    pub fn resolved_hyperscale_max_count(&self) -> Option<u64> {
        self.max_hyperscale_count
            .or(self.hyperscale_count)
            .or(self.min_hyperscale_count)
            .or(self.max_instances)
    }

    pub fn resolved_gateway_count(&self) -> Option<u64> {
        self.resolved_gateway_min_count()
    }

    pub fn resolved_hyperscale_count(&self) -> Option<u64> {
        self.resolved_hyperscale_min_count()
    }

    pub fn compatibility_min_instances(&self) -> Option<u64> {
        if let (Some(gateway_count), Some(hyperscale_count)) = (
            self.resolved_gateway_min_count(),
            self.resolved_hyperscale_min_count(),
        ) {
            Some(gateway_count.min(hyperscale_count))
        } else {
            self.min_instances
        }
    }

    pub fn compatibility_max_instances(&self) -> Option<u64> {
        if let (Some(gateway_count), Some(hyperscale_count)) = (
            self.resolved_gateway_max_count(),
            self.resolved_hyperscale_max_count(),
        ) {
            Some(gateway_count.max(hyperscale_count))
        } else {
            self.max_instances
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CliClusterProject {
    pub cluster_id: String,
    pub project_id: String,
    pub project_name: String,
    pub workspace_id: String,
}

async fn get_json<T: DeserializeOwned>(
    client: &Client,
    url: String,
    api_key: &str,
    action: &str,
) -> Result<T> {
    let response = client.get(&url).header("x-api-key", api_key).send().await?;
    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        return Err(eyre!("Failed to {action}: HTTP {status} {body}"));
    }
    Ok(response.json::<T>().await?)
}

pub async fn fetch_workspaces(
    client: &Client,
    base_url: &str,
    api_key: &str,
) -> Result<Vec<CliWorkspace>> {
    get_json(
        client,
        format!("{base_url}/api/cli/workspaces"),
        api_key,
        "fetch workspaces",
    )
    .await
}

pub async fn fetch_projects(
    client: &Client,
    base_url: &str,
    api_key: &str,
    workspace_id: &str,
) -> Result<Vec<CliProject>> {
    get_json(
        client,
        format!("{base_url}/api/cli/workspaces/{workspace_id}/projects"),
        api_key,
        "fetch projects",
    )
    .await
}

pub async fn fetch_project_details(
    client: &Client,
    base_url: &str,
    api_key: &str,
    project_id: &str,
) -> Result<CliProjectDetails> {
    get_json(
        client,
        format!("{base_url}/api/cli/projects/{project_id}"),
        api_key,
        "fetch project details",
    )
    .await
}

pub async fn fetch_project_clusters(
    client: &Client,
    base_url: &str,
    api_key: &str,
    project_id: &str,
) -> Result<CliProjectClusters> {
    get_json(
        client,
        format!("{base_url}/api/cli/projects/{project_id}/clusters"),
        api_key,
        "fetch project clusters",
    )
    .await
}

pub async fn fetch_workspace_clusters(
    client: &Client,
    base_url: &str,
    api_key: &str,
    workspace_id: &str,
) -> Result<CliWorkspaceClusters> {
    get_json(
        client,
        format!("{base_url}/api/cli/workspaces/{workspace_id}/clusters"),
        api_key,
        "fetch workspace clusters",
    )
    .await
}

pub async fn fetch_indexes_for_cluster(
    client: &Client,
    base_url: &str,
    api_key: &str,
    cluster_id: &str,
) -> Result<CliClusterIndexes> {
    get_json(
        client,
        format!("{base_url}/api/cli/enterprise-clusters/{cluster_id}/indexes"),
        api_key,
        "fetch cluster indexes",
    )
    .await
}

pub async fn fetch_enterprise_cluster_project(
    client: &Client,
    base_url: &str,
    api_key: &str,
    cluster_id: &str,
) -> Result<CliClusterProject> {
    get_json(
        client,
        format!("{base_url}/api/cli/enterprise-clusters/{cluster_id}/project"),
        api_key,
        "fetch enterprise cluster project",
    )
    .await
}

pub fn find_workspace_by_id<'a>(
    workspaces: &'a [CliWorkspace],
    id: &str,
) -> Option<&'a CliWorkspace> {
    workspaces.iter().find(|workspace| workspace.id == id)
}

pub fn find_workspace_by_slug<'a>(
    workspaces: &'a [CliWorkspace],
    slug: &str,
) -> Option<&'a CliWorkspace> {
    workspaces
        .iter()
        .find(|workspace| workspace.url_slug == slug)
}

pub fn find_project_by_id<'a>(projects: &'a [CliProject], id: &str) -> Option<&'a CliProject> {
    projects.iter().find(|project| project.id == id)
}

pub fn find_project_by_name<'a>(projects: &'a [CliProject], name: &str) -> Option<&'a CliProject> {
    projects.iter().find(|project| project.name == name)
}

pub fn find_enterprise_cluster_by_id<'a>(
    clusters: &'a [CliEnterpriseCluster],
    id: &str,
) -> Option<&'a CliEnterpriseCluster> {
    clusters.iter().find(|cluster| cluster.cluster_id == id)
}

/// List the Enterprise clusters available for a project (preferred) or workspace.
pub async fn list_clusters_for_context(
    client: &Client,
    base_url: &str,
    api_key: &str,
    project_id: Option<&str>,
    workspace_id: Option<&str>,
) -> Result<Vec<CliEnterpriseCluster>> {
    if let Some(project_id) = project_id {
        Ok(
            fetch_project_clusters(client, base_url, api_key, project_id)
                .await?
                .enterprise,
        )
    } else if let Some(workspace_id) = workspace_id {
        Ok(
            fetch_workspace_clusters(client, base_url, api_key, workspace_id)
                .await?
                .enterprise,
        )
    } else {
        Err(eyre!(
            "No workspace selected. Run 'helix workspace switch <workspace>'."
        ))
    }
}

/// A cluster resolved to its full metadata plus the project/workspace it belongs to.
pub struct ResolvedEnterpriseCluster {
    pub cluster: CliEnterpriseCluster,
    pub project_id: String,
    pub project_name: String,
    pub workspace_id: Option<String>,
}

/// Resolve a cluster ID to its full record and owning project/workspace.
///
/// Prefers the caller-supplied IDs; otherwise looks up the cluster's project via
/// `fetch_enterprise_cluster_project`. The full cluster record is then pulled from
/// the project's cluster list.
pub async fn resolve_enterprise_cluster(
    client: &Client,
    base_url: &str,
    api_key: &str,
    cluster_id: &str,
    known_project_id: Option<&str>,
    known_workspace_id: Option<&str>,
) -> Result<ResolvedEnterpriseCluster> {
    let (project_id, project_name, workspace_id) = if let Some(project_id) = known_project_id {
        (
            project_id.to_string(),
            None,
            known_workspace_id.map(str::to_string),
        )
    } else {
        let cluster_project =
            fetch_enterprise_cluster_project(client, base_url, api_key, cluster_id).await?;
        (
            cluster_project.project_id,
            Some(cluster_project.project_name),
            Some(cluster_project.workspace_id),
        )
    };
    let project_clusters = fetch_project_clusters(client, base_url, api_key, &project_id).await?;
    let cluster = find_enterprise_cluster_by_id(&project_clusters.enterprise, cluster_id)
        .cloned()
        .ok_or_else(|| {
            eyre!("Enterprise cluster '{cluster_id}' was not found in project '{project_id}'")
        })?;

    Ok(ResolvedEnterpriseCluster {
        project_id: project_clusters.project_id,
        project_name: project_name.unwrap_or(project_clusters.project_name),
        workspace_id,
        cluster,
    })
}
