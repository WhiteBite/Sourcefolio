"""GitHub REST API adapters (stdlib urllib only)."""

from sourcefolio.adapters.github.repos import (
    GitHubRepoClient,
    GitHubRepoConfig,
    GitHubRepoError,
)

__all__ = ["GitHubRepoClient", "GitHubRepoConfig", "GitHubRepoError"]
