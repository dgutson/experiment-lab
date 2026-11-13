# The owner's pages

The owner reads the same records, experiments, runs and views at http://127.0.0.1:5000/lab, and
searches them on its Experiments and Records pages, which search with `holds_words` too.

The pages link a `file:` item, a `~/` or absolute path in any text, and a table cell, such as a
run's param or tag, that is all such a path, to the file when it is under a project's folder or
files folder or is rec's copy of a cited file, with a player for audio and the image for an image; a relative path is not linked.

Record texts, notes, descriptions and `.md` files there are Markdown. A ```` ```mermaid ```` block
is drawn as a diagram by mermaid 11.17.2, the version mmdc uses, so check a diagram with
`mmdc -i x.mmd -o x.svg` before recording it.
