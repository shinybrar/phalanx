"""Models of the Phalanx environment and application configurations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

ENVIRONMENTS_DIR = "science-platform"
"""Directory of the environments Helm chart in Phalanx."""

APPS_DIR = "services"
"""Root directory of the application Helm charts in Phalanx."""


@dataclass(kw_only=True)
class Application:
    """A model for a Phalanx-configured application."""

    name: str
    """Name of the application.

    This name is used to label directories, etc.
    """

    values: Dict[str, Dict]
    """The parsed Helm values for each environment."""

    active_environments: List[str] = field(default_factory=list)
    """Environments where this application is active."""

    namespace: str
    """Kubernetes namespace"""

    @classmethod
    def load(
        cls, *, app_dir: Path, root_dir: Path, env_values: Dict[str, Dict]
    ) -> Application:
        """Load an application from the Phalanx repository.

        Parameters
        ----------
        app_dir : `pathlib.Path`
            The application's directory (where its Helm chart is located
            in Phalanx).
        env_values : `dict`
            The Helm values for each environment, keyed by the environment
            name. This data determines where the application is active.
        """
        app_name = app_dir.name

        # Load the app's values files for each environment
        values: Dict[str, Dict] = {}
        for values_path in app_dir.glob("values-*.yaml"):
            env_name = values_path.stem.removeprefix("values-")
            values[env_name] = yaml.safe_load(values_path.read_text())

        # Determine what environments use this app based on the environment's
        # values file.
        active_environments: List[str] = []
        for env_name, env_configs in env_values.items():
            if app_name == "argocd":
                active_environments.append(env_name)
                continue

            try:
                reformatted_name = app_name.replace("-", "_")
                if env_configs[reformatted_name]["enabled"] is True:
                    active_environments.append(env_name)
            except KeyError:
                pass
        active_environments.sort()

        # Open the Application Helm definition to get namespace info
        namespace = "Unknown"
        app_template_path = root_dir.joinpath(
            ENVIRONMENTS_DIR, "templates", f"{app_name}-application.yaml"
        )
        if app_template_path.is_file():
            app_template = app_template_path.read_text()
            # Extract the namespace from the Helm template
            pattern = (
                r"destination:\n"
                r"[ ]+namespace:[ ]*[\"]?(?P<namespace>[a-zA-Z][\w-]+)[\"]?"
            )
            m = re.search(
                pattern, app_template, flags=re.MULTILINE | re.DOTALL
            )
            if m:
                namespace = m.group("namespace")
            else:
                print(f"Did not match template for namespace for {app_name}")
        else:
            print(f"Could not open app template for {app_name}")

        return cls(
            name=app_name,
            values=values,
            active_environments=active_environments,
            namespace=namespace,
        )


@dataclass(kw_only=True)
class Environment:
    """A model for an environment."""

    name: str
    """Name of the Phalanx environment.

    This name is used to label directories, values files, etc.
    """

    domain: str
    """The root domain where the environment is hosted."""

    vault_path_prefix: str
    """The Vault key prefix for this environment."""

    apps: List[Application]
    """The applications that are enabled for this service."""

    @property
    def argocd_url(self) -> Optional[str]:
        """Path to the Argo CD UI."""
        argocd = self.get_app("argocd")
        if argocd is None:
            return "N/A"

        try:
            return argocd.values[self.name]["argo-cd"]["server"]["config"][
                "url"
            ]
        except KeyError:
            # Environments like minikube don't expose an argo cd URL
            return "N/A"

    @property
    def argocd_rbac_csv(self) -> Optional[List[str]]:
        """The Argo CD RBAC table, as a list of CSV lines."""
        argocd = self.get_app("argocd")
        if argocd is None:
            return None

        try:
            rbac_csv = argocd.values[self.name]["argo-cd"]["server"][
                "rbacConfig"
            ]["policy.csv"]
            lines = [
                ",".join([f"``{item.strip()}``" for item in line.split(",")])
                for line in rbac_csv.splitlines()
            ]
            print(lines)
            return lines
        except KeyError:
            # Some environments may not configure an RBAC
            return None

    @property
    def identity_provider(self) -> str:
        """A description of the identity provider for Gafaelfawr."""
        gafaelfawr = self.get_app("gafaelfawr")
        if gafaelfawr is None:
            return "Unknown"

        config_values = gafaelfawr.values[self.name]["config"]
        if "cilogon" in config_values:
            return "CILogon"

        if "github" in config_values:
            return "GitHub"

        if "oidc" in config_values:
            return "OIDC"

        return "Unknown"

    @property
    def gafaelfawr_roles(self) -> List[Tuple[str, List[str]]]:
        """Gafaelfawr role mapping."""
        roles: List[Tuple[str, List[str]]] = []

        gafaelfawr = self.get_app("gafaelfawr")
        if gafaelfawr is None:
            return roles

        try:
            group_mapping = gafaelfawr.values[self.name]["config"][
                "groupMapping"
            ]
        except KeyError:
            return roles

        role_names = sorted(group_mapping.keys())
        for role_name in role_names:
            groups = group_mapping[role_name]
            roles.append((role_name, groups))

        return roles

    def get_app(self, name) -> Optional[Application]:
        """Get the named application."""
        for app in self.apps:
            if app.name == name:
                return app
        return None

    @classmethod
    def load(
        cls, *, values: Dict[str, Any], applications: List[Application]
    ) -> Environment:
        """Load an environment by inspecting the Phalanx repository."""
        # Extract name from dir/values-envname.yaml
        name = values["environment"]

        # Get Application instances active in this environment
        apps: List[Application] = []
        for app in applications:
            if app.name == "argocd":
                # argocd is a special case because it's not toggled per env
                apps.append(app)
                continue

            try:
                if values[app.name]["enabled"] is True:
                    apps.append(app)
            except KeyError:
                continue
        apps.sort(key=lambda a: a.name)

        return Environment(
            name=name,
            domain=values["fqdn"],
            vault_path_prefix=values["vault_path_prefix"],
            apps=apps,
        )


@dataclass(kw_only=True)
class Phalanx:
    """Root container for Phalanx data."""

    environments: List[Environment] = field(default_factory=list)
    """Phalanx environments."""

    apps: List[Application] = field(default_factory=list)
    """Phalanx applications."""

    @classmethod
    def load_phalanx(cls, root_dir: Path) -> Phalanx:
        """Load the Phalanx git repository.

        Parameters
        ----------
        root_dir : `pathlib.Path`
            The path for the root directory of a Phalanx repository clone.

        Returns
        -------
        phalanx : `Phalanx`
            A model of the Phalanx platform, including environment and
            application configuration.
        """
        apps: List[Application] = []
        envs: List[Environment] = []

        # Pre-load the values files for each environment
        env_values: Dict[str, Dict[str, Any]] = {}
        for env_values_path in root_dir.joinpath(ENVIRONMENTS_DIR).glob(
            "values-*.yaml"
        ):
            if not env_values_path.is_file():
                continue
            values = yaml.safe_load(env_values_path.read_text())
            name = values["environment"]
            env_values[name] = values

        # Gather applications
        for app_dir in root_dir.joinpath(APPS_DIR).iterdir():
            if not app_dir.is_dir():
                continue
            app = Application.load(
                app_dir=app_dir, env_values=env_values, root_dir=root_dir
            )
            apps.append(app)
        apps.sort(key=lambda a: a.name)

        # Gather environments
        for env_name, values in env_values.items():
            env = Environment.load(values=values, applications=apps)
            envs.append(env)

        return cls(environments=envs, apps=apps)
