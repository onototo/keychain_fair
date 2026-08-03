# Agent Instructions

## Encoding

- Treat every project text file as UTF-8 without BOM.
- Keep new and edited files in UTF-8 without BOM. Do not introduce Windows-1251, UTF-16, or mixed encodings.
- If PowerShell or another terminal displays Cyrillic text as mojibake, verify the file bytes as UTF-8 before changing content; terminal output alone is not evidence of a file encoding problem.
- Russian user-facing text may stay in the product where it is intentional. Prefer English for code comments.
- When applying patches around non-ASCII text, patch the file normally and preserve the existing UTF-8 content.
