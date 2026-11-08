"""Product variants: different ways of turning a boxer's movement into key presses.

Each variant is a folder in this package (variants/angled_camera/, ...). To add one, add a folder
with an __init__.py that defines a module-level VARIANT = YourVariant(). Nothing else is edited: this file
finds the folders by itself, so a new variant cannot change how an existing one behaves.

A variant that fails to load (a typo, a missing library) is reported by name and skipped. The others
still work.

    python play.py                 list the variants and whether each loaded
    python play.py <name> --live   play with one of them
"""
import importlib
import pkgutil

from variants.base import Variant


def discover():
    """Returns (variants, errors): {name: Variant} and {folder name: error text}."""
    found, errors = {}, {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        try:
            module = importlib.import_module(f"variants.{info.name}")
            variant = module.VARIANT
            if not isinstance(variant, Variant):
                raise TypeError("VARIANT must be an instance of variants.base.Variant")
            if not variant.name:
                raise ValueError("the variant has no name")
            if variant.name in found:
                raise ValueError(f"the name {variant.name!r} is already used by another variant")
            found[variant.name] = variant
        except Exception as e:  # one broken variant must not take the others down
            errors[info.name] = f"{type(e).__name__}: {e}"
    return found, errors


def get(name):
    """The named variant, or a ValueError that says what is available."""
    found, errors = discover()
    if name in found:
        return found[name]
    detail = f" (folder {name!r} failed to load: {errors[name]})" if name in errors else ""
    raise ValueError(f"no variant called {name!r}{detail}. Available: {', '.join(sorted(found)) or 'none'}")
