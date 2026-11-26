import sys
sys.path.insert(0, "/mnt/e/Documents/development/stable/hydra-git/src")
from hydra.wiki.look import themes as T
def v(d): return "".join(f"--{k.replace('_','-')}:{x};" for k, x in d.items())
for n in ("shore", "tide", "tan", "light"):
    light = T.THEMES[n]
    print(f":root[data-theme={n}]{{{v(light)}color-scheme:light}}")
    if n in T.DARK:
        print(f":root[data-theme={n}][data-mode=dark]{{{v({**light, **T.DARK[n]})}color-scheme:dark}}")
