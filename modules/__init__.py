"""
Module registry for Capactive platform modules.

Modules are self-contained feature packages that extend the platform
with domain-specific capabilities (proforma modeling, comp analysis, etc.).

Each module subclasses AbstractModule and is discovered automatically
when registered in INSTALLED_MODULES below.
"""

import importlib
import logging

logger = logging.getLogger(__name__)

# Register modules here — each entry is a dotted path to the module package
INSTALLED_MODULES = [
    'modules.proforma',
    'modules.inventory',
    'modules.sales_comps',
    'modules.scorecard',
    'modules.lease_analysis',
]


class ModuleRegistry:
    """Discovers and manages platform modules."""

    def __init__(self):
        self._modules = {}
        self._loaded = False

    def discover(self):
        """Import and register all installed modules."""
        for module_path in INSTALLED_MODULES:
            try:
                mod = importlib.import_module(f'.{module_path}', package='realestate_extractor')
                if hasattr(mod, 'module_instance'):
                    instance = mod.module_instance
                    self._modules[instance.name] = instance
                    logger.info(f'Registered module: {instance.name}')
                else:
                    logger.warning(f'Module {module_path} has no module_instance')
            except Exception as e:
                logger.warning(f'Failed to load module {module_path}: {e}')
        self._loaded = True

    def register_routes(self, app):
        """Register all module routes with the Flask app."""
        if not self._loaded:
            self.discover()
        for name, module in self._modules.items():
            try:
                module.register_routes(app)
                logger.info(f'Registered routes for module: {name}')
            except Exception as e:
                logger.warning(f'Failed to register routes for {name}: {e}')

    def get(self, name):
        """Get a module by name."""
        return self._modules.get(name)

    def list_modules(self):
        """Return list of registered module info dicts."""
        return [
            {
                'name': m.name,
                'display_name': m.display_name,
                'description': m.description,
                'version': m.version,
            }
            for m in self._modules.values()
        ]

    @property
    def modules(self):
        return dict(self._modules)


# Singleton registry
registry = ModuleRegistry()
