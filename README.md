# JEE Variant Builder

This package contains the original JEE project unchanged plus `build_jee.py`.

Run from the project root:
```bash
python build_jee.py
```

It creates a sibling folder named `JeeQuizBot-main/` and never edits the original
`JeeQuizBot-main/` folder.

You can also pass explicit source/output directories:
```bash
python build_jee.py /path/to/JeeQuizBot-main /path/to/JeeQuizBot-main
```

The builder changes JEE/JEE branding and JeeQuizRobot/JeeQuizRobot branding in
the copied text files, while skipping Python bytecode caches.
