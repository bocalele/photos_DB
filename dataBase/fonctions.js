    function collapse(d) {
        if (!d) return;
        if (d.children) {
            d.children.forEach(child => {
                collapse(child);
            });
            d._children = d.children;
            d.children = null;
        }
    }


    function update(source) {

        tree(root);

        let x0 = Infinity;
        let x1 = -Infinity;
        let y1 = -Infinity;

        root.each(d => {
            if (d.x < x0) x0 = d.x;
            if (d.x > x1) x1 = d.x;
            if (d.y > y1) y1 = d.y; 
        });

        const height = y1 + 200; 
        svg.attr("height", height);
        g.attr("transform", `translate(${-x0 + 100}, 50)`);

        const nodes = root.descendants();
        const links = root.links();

        // =====================
        // LINKS
        // =====================
        g.selectAll("path.link")
            .data(links)
            .join("path")
            .attr("class", "link")
            .attr("fill", "none")
            .attr("stroke", "#999")
            .attr("stroke-width", 2)
            .attr("d", d3.linkVertical()
                .x(d => d.x)
                .y(d => d.y)
            );


        const node = g.selectAll("g.node")
            .data(nodes, d => d.data.id)
            .join(
                enter => {const g = enter.append("g") 
                    .attr("class", "node");
                    g.append("circle");
                    g.append("text")
                        .attr("dy", "0.35em")
                        .attr("x", 15);
                    return g;
                },
                update => update,
                exit => exit.remove()
            );
        node.attr("transform", d => `translate(${d.x},${d.y})`);

        node.select("circle")
            .attr("r", d => d.depth === 0 ? 16 : 10)
            .attr("fill", d => d.data.id === selectedId ? "#ff4d4d" : "#1f77b4")
            .on("click", (event, d) => {
                if (!d) return;
                selectedId = d.data.id;
                if (d.children) {
                    d._children = d.children;
                    d.children = null;
                } else if (d._children) {
                    d.children = d._children;
                    d._children = null;
                }
                update(d);
            });

        node.select("text")
            .text(d => d.data.name)
            .style("font-size", "14px")
            .style("font-weight", d => d.data.id === selectedId ? "bold" : "normal");

            // TOOLTIP
        node.selectAll("title")
            .data(d => [d])
            .join("title")
            .text(d => d.data.fullname);

// 1. Bouton ENFANTS (Bas)
        const btnChildren = node.selectAll("g.toggle-children")
            .data(d => d && (d.children || d._children) ? [d] : [])
            .join("g")
            .attr("class", "toggle-children")
            .attr("transform", "translate(0, 35)")
            .style("cursor", "pointer")
            .on("click", (event, d) => {
                event.stopPropagation();
                if (d.children) { d._children = d.children; d.children = null; } 
                else { d.children = d._children; d._children = null; }
                update(d);
            });

        btnChildren.append("circle").attr("r", 8).attr("fill", "#f5f1f1ff").attr("stroke", "#350ae4ff");
        btnChildren.append("text").attr("text-anchor", "middle").attr("dy", "0.35em").style("font-size", "12px")
            .text(d => d.children ? "-" : "+");

        // 2. Bouton PARENTS (Haut)
        const btnParents = node.selectAll("g.toggle-parents")
            .data(d => d && (d.parents || d._parents) ? [d] : []) // Nécessite que le JSON envoie "parents"
            .join("g")
            .attr("class", "toggle-parents")
            .attr("transform", "translate(0, -35)")
            .style("cursor", "pointer")
            .on("click", (event, d) => {
                event.stopPropagation();
                if (d.parents) { d._parents = d.parents; d.parents = null; } 
                else { d.parents = d._parents; d._parents = null; }
                update(d);
            });

        btnParents.append("circle").attr("r", 8).attr("fill", "#e51919ff").attr("stroke", "#350ae4ff");
        btnParents.append("text").attr("text-anchor", "middle").attr("dy", "0.35em").style("font-size", "12px")
            .text(d => d.parents ? "-" : "+");

    }
