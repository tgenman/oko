"""An empty current store for isolated reader scenarios."""

def publication_root(tmp_path):
    root=tmp_path/'data'
    root.mkdir(parents=True,exist_ok=True)
    return root
