# Use Kosika's own maths and Designs first
When: kosika, .kos, math, maths, formula, equation, calculate, compute, solve, derivative, integral, plot, graph, chart, curve, surface, draw, drawing, diagram, picture, shape, visual, visualise, visualize, design, designs, art, canvas, svg, neuron, brain

Kosika has its own maths and its own drawing, and a Kosika agent reaches
for them first. Write a number, a formula or a picture by hand, or bring in
another library, only when Kosika's own cannot do it, and say so.

Maths: `import "std/math.kos"`. `parse(text).expr` reads a formula; `at(expr, names, values)`,
`calc` and `subst` compute it; `solve`, `roots`, `solve_system`, `deriv`,
`integrate`, `expand`, `simplify` and `exact` do the algebra; `to_mathml`
and `to_latex` write it out. Let these compute every number. Never type a
result you worked out yourself into code, a test or a reply.

Drawings: `import "std/designs_plot.kos"`. `pl_for(text)` draws a curve,
surface, polar or parametric curve, complex map, Taylor series, integral,
flow or spacetime from the words of an ask, and gives back a `PlFigure`
with an `svg`, a live 3D view and a page. Build your own 3D scene from
`PlScene` quads and lines, then `pl_scene_svg` and `pl_scene_live_with`
draw it in Kosika's white lines; `pl_regions`, `pl_region_hex` colour the
Brain's parts. `std/designs_brain.kos` and `std/designs_neurons.kos` draw
the Brain and its neurons.

Designs: `import "std/designs.kos"`. `design({text, palette, place, kind})` makes a whole piece:
art, a diagram, a mind map, a canvas scene or a science drawing, in the
Designs style (dark ground, bright region colours, short text).

Tests check what Kosika computed against a value you can see is right
(`expect at(parse("x^2").expr, ["x"], [3]) == 9`), not against a number copied from a
model.
