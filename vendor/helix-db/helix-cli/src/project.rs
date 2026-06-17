use crate::config::HelixConfig;
use crate::errors::ProjectError;
use eyre::{Result, eyre};
use std::env;
use std::path::{Path, PathBuf};

pub struct ProjectContext {
    pub root: PathBuf,
    pub config: HelixConfig,
    pub helix_dir: PathBuf,
}

impl ProjectContext {
    pub fn find_and_load(start_dir: Option<&Path>) -> Result<Self, ProjectError> {
        Self::load_with(start_dir, true)
    }

    /// Like [`find_and_load`](Self::find_and_load), but tolerates a `helix.toml` that defines
    /// zero instances. Used by `helix add` so it can re-add the first instance after the last
    /// one was deleted.
    pub fn find_and_load_allow_no_instances(
        start_dir: Option<&Path>,
    ) -> Result<Self, ProjectError> {
        Self::load_with(start_dir, false)
    }

    fn load_with(start_dir: Option<&Path>, require_instances: bool) -> Result<Self, ProjectError> {
        let start = match start_dir {
            Some(dir) => dir.to_path_buf(),
            None => env::current_dir().map_err(|source| ProjectError::CurrentDir { source })?,
        };

        let root = find_project_root(&start)?;
        let config_path = root.join("helix.toml");
        let config = if require_instances {
            HelixConfig::from_file(&config_path)?
        } else {
            HelixConfig::from_file_allow_no_instances(&config_path)?
        };
        let helix_dir = root.join(".helix");

        Ok(Self {
            root,
            config,
            helix_dir,
        })
    }

    pub fn instance_workspace(&self, instance_name: &str) -> PathBuf {
        self.helix_dir.join(instance_name)
    }

    pub fn ensure_instance_dir(&self, instance_name: &str) -> Result<(), ProjectError> {
        let workspace = self.instance_workspace(instance_name);
        std::fs::create_dir_all(&workspace).map_err(|source| ProjectError::CreateDir {
            path: workspace,
            source,
        })?;
        Ok(())
    }
}

fn find_project_root(start: &Path) -> Result<PathBuf, ProjectError> {
    let mut current = start.to_path_buf();
    loop {
        if current.join("helix.toml").exists() {
            return Ok(current);
        }

        match current.parent() {
            Some(parent) => current = parent.to_path_buf(),
            None => break,
        }
    }

    Err(ProjectError::ConfigNotFound {
        start: start.to_path_buf(),
    })
}

pub fn get_helix_cache_dir() -> Result<PathBuf> {
    if let Ok(override_dir) = std::env::var("HELIX_CACHE_DIR") {
        let helix_dir = PathBuf::from(override_dir);
        std::fs::create_dir_all(&helix_dir)?;
        return Ok(helix_dir);
    }

    let home = dirs::home_dir().ok_or_else(|| eyre!("Cannot find home directory"))?;
    let helix_dir = home.join(".helix");
    std::fs::create_dir_all(&helix_dir)?;
    Ok(helix_dir)
}
