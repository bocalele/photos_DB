import pandas as pd, networkx as nx
from streamlit_agraph import Node, Edge
import streamlit as st


def build_graph(df):
    G = nx.DiGraph()

    for _, row in df.iterrows():
        G.add_node(
            row["id_people"],
            nom=row["nom"],
            prenom=row["prenom"],
            photo=row.get("photo", "")
        )

    for _, row in df.iterrows():
        child = row["id_people"]

        if pd.notna(row["id_pere"]):
            G.add_edge(row["id_pere"], child)

        if pd.notna(row["id_mere"]):
            G.add_edge(row["id_mere"], child)

    return G


def build_agraph(G, nodes_ids, edges_list, person_id):
    nodes = []
    edges = []

    for n, level in nodes_ids:
        data = G.nodes[n]
        label = f"{data['prenom']}\n{data['nom']}"
        size = 40 if n == person_id else 25

        nodes.append(
            Node( id=str(int(n)), label=label, size=size,
                shape="circularImage" if data.get("photo") else "dot",
                # image=data.get("photo") if data.get("photo") else None,
                level = level,
                color={ "background": "#FF8C00" if n == person_id else "#1f77b4","border": "#333" },
                font={"color": "black"})
            )

    present_node_ids = {n for n, level in nodes_ids}
    for u, v in edges_list:
        if u in present_node_ids and v in present_node_ids:
            edges.append(Edge(source=str(int(u)), target=str(int(v)),
            smooth=True,
            color="#888"))
    
    return (nodes, edges)

def compute_levels(G, root, visible_nodes):
    levels = {root: 0}
    queue = [root]

    while queue:
        current = queue.pop(0)
        current_level = levels[current]

        # ⬆ parents → niveau -1
        for p in G.predecessors(current):
            if p in visible_nodes and p not in levels:
                levels[p] = current_level - 1
                queue.append(p)

        # ⬇ enfants → niveau +1
        for c in G.successors(current):
            if c in visible_nodes and c not in levels:
                levels[c] = current_level + 1
                queue.append(c)

    min_level = min(levels.values())
    levels = {k: v - min_level for k, v in levels.items()}  
    return levels

def expand_graph(G, person_id, st):
    state = st.session_state.node_states.get(person_id, {})

    show_parents = state.get("parents", False)
    show_children = state.get("children", False)
    show_siblings = state.get("siblings", False)

    nodes = st.session_state.visible_nodes
    edges = st.session_state.visible_edges

    nodes.add(person_id)

    if show_parents:
        for p in G.predecessors(person_id):
            nodes.add(p)
            edges.add((p, person_id))

    if show_children:
        for c in G.successors(person_id):
            nodes.add(c)
            edges.add((person_id, c))

    if show_siblings:
        parents = list(G.predecessors(person_id))
        for p in parents:
            for s in G.successors(p):
                if s != person_id:
                    nodes.add(s)
                    edges.add((p, s))

def build_tree(G, root):
    def build_node(n):
        children = [build_node(child) for child in G.successors(n)]

        node = {
            "id": int(n),
            "name": f"{G.nodes[n]['prenom']}",
            "fullname": f"{G.nodes[n]['prenom']} {G.nodes[n]['nom']}"
        }

        if children:
            node["children"] = children

        return node
    return build_node (root)

def build_down_tree(G, root):
    def build_down_node(n):
        children = [build_down_node(child) for child in G.successors(n)]

        node = {
            "id": int(n),
            "name": f"{G.nodes[n]['prenom']}",
            "fullname": f"{G.nodes[n]['prenom']} {G.nodes[n]['nom']}"
        }

        if children:
            node["children"] = children

        return node
    return build_down_node (root)

def build_up_tree(G, root):
    def build_up_node(n):
        parent = [build_up_node(child) for child in G.predecessors(n)]

        node = {
            "id": int(n),
            "name": f"{G.nodes[n]['prenom']}",
            "fullname": f"{G.nodes[n]['prenom']} {G.nodes[n]['nom']}"
        }

        if parent:
            node["parent"] = parent

        return node
    return build_up_node (root)

def build_full_tree(G, root):
    def build_node(n, visited=set()):
        if n in visited:
            return None
        visited.add(n)

        node = {
            "id": int(n),
            "name": G.nodes[n]['prenom'],
            "fullname": f"{G.nodes[n]['prenom']} {G.nodes[n]['nom']}"
        }

        children = []

        # ⬇ ENFANTS
        for c in G.successors(n):
            child_node = build_node(c, visited)
            if child_node:
                children.append(child_node)

        # ⬆ PARENTS (on les ajoute aussi comme children mais marqués)
        for p in G.predecessors(n):
            parent_node = build_node(p, visited)
            if parent_node:
                parent_node["type"] = "parent"
                children.append(parent_node)

                # 👇 FRATRIE
                for s in G.successors(p):
                    if s != n:
                        sibling_node = build_node(s, visited)
                        if sibling_node:
                            sibling_node["type"] = "sibling"
                            children.append(sibling_node)

        if children:
            node["children"] = children

        return node

    return build_node(root)