"""
stl_to_avl.py

Extract an AVL planform (SECTION cards + per-section airfoil .dat files) from a
closed STL exported from Onshape.

AVL is a vortex lattice on the CAMBER SURFACE. It discards thickness entirely.
The only things it consumes from your geometry are:
    Xle, Yle, Zle, chord, incidence (Ainc), and the section camber line.
Everything else in the mesh is thrown away. Do not mistake a converged AVL run
for a validated BWB centrebody.

Usage:
    python stl_to_avl.py bwb.stl --out avl/ --n-sections 21 --units mm

Assumed axes (check yours and use --swap-axes if wrong):
    x = streamwise, aft positive
    y = spanwise, starboard positive
    z = up
"""

import argparse
import os

import numpy as np

# ----------------------------------------------------------------------------
# Section geometry (pure numpy, unit-testable without trimesh)
# ----------------------------------------------------------------------------


def order_loop(xz):
    """Return the closed section polygon as an ordered (N,2) array.

    trimesh returns ordered vertices per closed entity already; this is a
    defensive re-order for cases where the section comes back as unordered
    segments. Uses a simple angular sort about the centroid, which is only
    valid for star-shaped sections. Aerofoils are star-shaped about their
    mid-chord point in practice, but verify by plotting.
    """
    c = xz.mean(axis=0)
    ang = np.arctan2(xz[:, 1] - c[1], xz[:, 0] - c[0])
    return xz[np.argsort(ang)]


def le_te_indices(xz):
    """Leading and trailing edge indices, taken as extreme x.

    TODO-VERIFY: for a highly swept centrebody with a blunt, rounded LE this
    picks the extreme facet vertex, not the true LE stagnation-line point.
    Faceting error here propagates straight into chord and twist.
    """
    return int(np.argmin(xz[:, 0])), int(np.argmax(xz[:, 0]))


def normalise_section(xz):
    """Map a section polygon into chord-aligned, unit-chord coordinates.

    Returns (xi, zeta, le, chord, twist_deg) where xi in [0,1] along the chord
    line and zeta is the perpendicular offset, both normalised by chord.
    """
    i_le, i_te = le_te_indices(xz)
    le, te = xz[i_le], xz[i_te]
    v = te - le
    chord = float(np.hypot(v[0], v[1]))
    e = v / chord
    n = np.array([-e[1], e[0]])

    d = xz - le
    xi = d @ e / chord
    zeta = d @ n / chord

    # AVL Ainc is section incidence, positive leading-edge-up.
    twist_deg = float(-np.degrees(np.arctan2(v[1], v[0])))
    return xi, zeta, le, chord, twist_deg


def split_surfaces(xi, zeta, n_pts=81):
    """Split the closed loop into upper and lower branches and resample.

    Returns (xs, z_upper, z_lower) on a cosine-clustered chordwise grid.
    """
    i_le = int(np.argmin(xi))
    i_te = int(np.argmax(xi))

    idx = np.roll(np.arange(len(xi)), -i_le)
    k = int(np.where(idx == i_te)[0][0])
    branch_a = idx[: k + 1]
    branch_b = np.concatenate([idx[k:], idx[:1]])[::-1]

    xs = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, n_pts)))

    def resample(branch):
        o = np.argsort(xi[branch])
        return np.interp(xs, xi[branch][o], zeta[branch][o])

    za, zb = resample(branch_a), resample(branch_b)
    if za.mean() >= zb.mean():
        return xs, za, zb
    return xs, zb, za


def write_selig(path, name, xs, z_up, z_lo):
    """Write a Selig-format .dat: TE -> upper -> LE -> lower -> TE.

    AVL reads this via AFILE and extracts the camber line itself. Letting AVL
    do the camber extraction is more robust than doing it here.
    """
    top = np.column_stack([xs, z_up])[::-1]
    bot = np.column_stack([xs, z_lo])[1:]
    pts = np.vstack([top, bot])
    with open(path, "w") as f:
        f.write(f"{name}\n")
        for x, z in pts:
            f.write(f"{x:12.7f} {z:12.7f}\n")


# ----------------------------------------------------------------------------
# Reference quantities
# ----------------------------------------------------------------------------


def projected_planform_area(face_normals, face_areas):
    """Projected planform area from upward-facing facets only.

    S = sum over faces with n_z > 0 of (A_face * n_z). This is exact for a
    closed mesh with no overhanging upper surface, which holds for a BWB.
    """
    up = face_normals[:, 2] > 0.0
    return float(np.sum(face_areas[up] * face_normals[up, 2]))


def mac_and_quarter_chord(y, chord, x_le):
    """Mean aerodynamic chord and its quarter-chord x location.

    cbar = (2/S) * int_0^{b/2} c(y)^2 dy
    x_cbar = (2/S) * int_0^{b/2} c(y) * x_le(y) dy
    Trapezoidal over the extracted stations. Only valid on the half-span.
    """
    s_half = np.trapezoid(chord, y)
    cbar = float(np.trapezoid(chord**2, y) / s_half)
    x_cbar = float(np.trapezoid(chord * x_le, y) / s_half)
    return cbar, x_cbar + 0.25 * cbar, 2.0 * s_half


# ----------------------------------------------------------------------------
# Main pipeline (requires trimesh)
# ----------------------------------------------------------------------------


def parse_axes(spec, extents=None):
    """Parse an axis remap into (perm, signs).

    Full form, three axes in order streamwise, spanwise, up:
        'xyz'  no change
        'xzy'  source z is spanwise, source y is up
        'xz-y' same, but source y points down so flip it

    Short form, one axis meaning "this is the spanwise axis":
        'z' or '-z'
    The remaining two are assigned by extent: the longer becomes streamwise,
    the shorter becomes up. Requires `extents`.
    """
    tokens, i = [], 0
    while i < len(spec):
        s = 1.0
        if spec[i] in "+-":
            s = -1.0 if spec[i] == "-" else 1.0
            i += 1
        tokens.append(("xyz".index(spec[i]), s))
        i += 1

    if len(tokens) == 1:
        if extents is None:
            raise ValueError("short axis spec needs mesh extents")
        span_ax, span_sign = tokens[0]
        rest = [k for k in (0, 1, 2) if k != span_ax]
        rest.sort(key=lambda k: extents[k], reverse=True)
        stream_ax, up_ax = rest
        perm = [stream_ax, span_ax, up_ax]
        signs = [1.0, span_sign, 1.0]
        print(f"  short spec {spec!r}: spanwise={'xyz'[span_ax]}, "
              f"streamwise={'xyz'[stream_ax]}, up={'xyz'[up_ax]} "
              f"(inferred from extents)")
        print("  if the model comes out upside down, pass the full spec with "
              f"a minus, e.g. '{'xyz'[stream_ax]}{'xyz'[span_ax]}-{'xyz'[up_ax]}'")
        return np.array(perm), np.array(signs)

    if len(tokens) != 3:
        raise ValueError(
            f"bad axis spec {spec!r}. Give either one letter (the spanwise "
            "axis, e.g. 'z') or all three in order streamwise, spanwise, up "
            "(e.g. 'xzy')."
        )

    perm = [t[0] for t in tokens]
    signs = [t[1] for t in tokens]
    if sorted(perm) != [0, 1, 2]:
        raise ValueError(f"bad axis spec {spec!r}; needs each of x, y, z once")
    return np.array(perm), np.array(signs)


def _chord_at(mesh, y):
    sec = mesh.section(plane_origin=[0.0, y, 0.0], plane_normal=[0.0, 1.0, 0.0])
    if sec is None:
        return np.nan
    loop = max([np.asarray(p) for p in sec.discrete], key=len)
    xz = loop[:, [0, 2]]
    return float(xz[:, 0].max() - xz[:, 0].min())


def symmetry_check(mesh, n=41):
    """Locate the spanwise symmetry plane and test whether it is a real one.

    Returns (y0, residual). y0 is the bounding-box midpoint. residual is the
    RMS mismatch between chord(y0+s) and chord(y0-s), normalised by the mean
    chord. Small residual means the model is merely TRANSLATED off centre and
    recentring fixes it. Large residual means the geometry is genuinely
    asymmetric and YDUPLICATE in AVL is invalid.
    """
    ylo, yhi = mesh.bounds[0, 1], mesh.bounds[1, 1]
    y0 = 0.5 * (ylo + yhi)
    s_max = 0.5 * (yhi - ylo)
    s = np.linspace(0.02 * s_max, 0.98 * s_max, n)
    cp = np.array([_chord_at(mesh, y0 + v) for v in s])
    cm = np.array([_chord_at(mesh, y0 - v) for v in s])
    ok = np.isfinite(cp) & np.isfinite(cm)
    if ok.sum() < 5:
        return y0, np.nan
    resid = float(np.sqrt(np.mean((cp[ok] - cm[ok]) ** 2)) /
                  np.mean(0.5 * (cp[ok] + cm[ok])))
    return y0, resid


def extract(stl_path, n_sections, scale, cluster, axes="xyz", recentre=True, diag=None):
    """Extract sections, Sref and half-span from an STL.

    `diag`, if a dict is passed, is filled with the diagnostics this function
    otherwise only prints (watertight status, symmetry residual, skipped
    stations, wetted mesh area). The pipeline gates on those values, and
    scraping them back out of stdout would mean a change to a print statement
    could silently switch a safety check off. Purely additive: callers that
    omit it see no change in behaviour.
    """
    import trimesh

    if diag is None:
        diag = {}

    mesh = trimesh.load(stl_path, process=True, force="mesh")

    perm, signs = parse_axes(axes, extents=mesh.extents)
    diag["axes_spec"] = axes
    diag["axes_perm"] = [int(v) for v in perm]
    diag["axes_signs"] = [float(v) for v in signs]
    diag["axes_remapped"] = not (np.all(perm == [0, 1, 2]) and np.all(signs == 1.0))
    if diag["axes_remapped"]:
        mesh.vertices = mesh.vertices[:, perm] * signs
        mesh.fix_normals()
        print(f"  remapped axes with --axes {axes}")

    mesh.apply_scale(scale)

    # Total wetted area of the closed mesh, in the scaled (metre) units.
    diag["mesh_area_m2"] = float(mesh.area)
    diag["extents_m"] = [float(v) for v in mesh.extents]

    diag["watertight"] = bool(mesh.is_watertight)
    diag["euler_number"] = int(mesh.euler_number)
    if not mesh.is_watertight:
        print("WARNING: mesh is not watertight. Sections may be broken loops.")
        print(f"         euler_number={mesh.euler_number}")

    y0, resid = symmetry_check(mesh)
    diag["symmetry_y0"] = float(y0)
    diag["symmetry_residual"] = float(resid)
    span = mesh.bounds[1, 1] - mesh.bounds[0, 1]
    print(f"  symmetry plane at y = {y0:+.4f} "
          f"({100*abs(y0)/span:.3f} % of span off origin)")
    print(f"  mirror residual = {resid:.4f} of mean chord")
    if resid > 0.02:
        print("  WARNING: the geometry is genuinely asymmetric, not just")
        print("           translated. YDUPLICATE in AVL mirrors about y=0 and")
        print("           will not represent this model. Fix the CAD.")
    if recentre and abs(y0) > 1e-6 * span:
        mesh.vertices[:, 1] -= y0
        print(f"  recentred the model by {-y0:+.4f} in span")

    # Half-span from the true extent, NOT max(|y_min|, |y_max|), which
    # silently doubles the larger half of an off-centre model.
    b_half = 0.5 * (mesh.bounds[1, 1] - mesh.bounds[0, 1])

    # Cluster stations toward the root, where a BWB chord and twist change
    # fastest. cluster=1.0 gives uniform, higher values push toward the root.
    t = np.linspace(0.0, 1.0, n_sections)
    frac = t**cluster
    stations = 1e-4 * b_half + frac * (0.995 * b_half - 1e-4 * b_half)

    out, skipped = [], []
    for y in stations:
        sec = mesh.section(plane_origin=[0.0, y, 0.0], plane_normal=[0.0, 1.0, 0.0])
        if sec is None:
            print(f"  skipped y={y:.4f}: no intersection")
            skipped.append(float(y))
            continue
        loops = [np.asarray(p) for p in sec.discrete]
        loop = max(loops, key=len)
        xz = loop[:, [0, 2]]
        xi, zeta, le, chord, twist = normalise_section(xz)
        xs, zu, zl = split_surfaces(xi, zeta)
        out.append(
            dict(y=float(y), x_le=float(le[0]), z_le=float(le[1]),
                 chord=chord, twist=twist, xs=xs, zu=zu, zl=zl,
                 tc=float(np.max(zu - zl)))
        )

    sref = projected_planform_area(mesh.face_normals, mesh.area_faces)
    diag["n_sections_requested"] = int(n_sections)
    diag["n_sections_extracted"] = len(out)
    diag["skipped_stations"] = skipped
    diag["sref_projected_m2"] = float(sref)
    diag["b_half_m"] = float(b_half)
    return out, sref, b_half


def write_avl(sections, sref, b_half, outdir, name, mach, nchord, cspace):
    os.makedirs(os.path.join(outdir, "sections"), exist_ok=True)

    y = np.array([s["y"] for s in sections])
    c = np.array([s["chord"] for s in sections])
    xle = np.array([s["x_le"] for s in sections])
    cbar, x_quarter, s_from_chords = mac_and_quarter_chord(y, c, xle)

    print(f"  Sref (projected facets) = {sref:.4f}")
    print(f"  Sref (chord integral)   = {s_from_chords:.4f}")
    print("  If these disagree by more than a few percent, your sections are "
          "not capturing the planform. Investigate before trusting AVL.")

    bref = 2.0 * b_half
    ar = bref**2 / sref
    print(f"  b = {bref:.4f}, MAC = {cbar:.4f}, AR = {ar:.3f}")
    print(f"  t/c range = {min(s['tc'] for s in sections):.3f} to "
          f"{max(s['tc'] for s in sections):.3f}")

    lines = [
        name,
        f"{mach:.4f}                      | Mach",
        "0     0     0.0           | iYsym  iZsym  Zsym",
        f"{sref:.6f} {cbar:.6f} {bref:.6f}   | Sref Cref Bref",
        f"{x_quarter:.6f} 0.0 0.0   | Xref Yref Zref (25% MAC)",
        "0.0                       | CDp",
        "#",
        "SURFACE",
        "Centrebody_Wing",
        f"{nchord}  {cspace:.1f}",
        "#",
        "YDUPLICATE",
        "0.0",
        "#",
        "ANGLE",
        "0.0",
        "#",
    ]

    for i, s in enumerate(sections):
        fname = f"sections/sec_{i:02d}.dat"
        write_selig(os.path.join(outdir, fname), f"sec_{i:02d}", s["xs"], s["zu"], s["zl"])
        lines += [
            "SECTION",
            "#Xle      Yle      Zle      Chord    Ainc   Nspan  Sspace",
            f"{s['x_le']:.6f} {s['y']:.6f} {s['z_le']:.6f} "
            f"{s['chord']:.6f} {s['twist']:.4f}  3  1.0",
            "AFILE",
            fname,
            "#",
        ]

    path = os.path.join(outdir, f"{name}.avl")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("stl")
    p.add_argument("--out", default="avl")
    p.add_argument("--name", default="bwb")
    p.add_argument("--n-sections", type=int, default=21)
    p.add_argument("--units", choices=["m", "mm", "in"], default="m")
    p.add_argument("--cluster", type=float, default=1.6,
                   help="1.0 uniform, higher clusters stations toward the root")
    p.add_argument("--mach", type=float, default=0.0)
    p.add_argument("--nchord", type=int, default=14)
    p.add_argument("--cspace", type=float, default=1.0,
                   help="AVL chordwise spacing code; 1.0 = cosine. VERIFY sign "
                        "convention against the AVL manual.")
    p.add_argument("--axes", default="xyz",
                   help="which source axes are streamwise, spanwise, up. "
                        "e.g. 'xzy' or 'x-zy'. Run diagnose_stl.py first.")
    p.add_argument("--force", action="store_true",
                   help="write the .avl even if the sanity checks fail")
    p.add_argument("--no-recentre", action="store_true",
                   help="do not shift the model onto its symmetry plane")
    a = p.parse_args()

    scale = {"m": 1.0, "mm": 1e-3, "in": 0.0254}[a.units]
    os.makedirs(a.out, exist_ok=True)
    sections, sref, b_half = extract(a.stl, a.n_sections, scale, a.cluster,
                                     a.axes, recentre=not a.no_recentre)

    tc = [s["tc"] for s in sections]
    if max(tc) > 0.60 and not a.force:
        raise SystemExit(
            f"\nABORT: max t/c = {max(tc):.3f}. No aerofoil section exceeds "
            "about 0.40.\nYour axis convention is almost certainly wrong. "
            "Run diagnose_stl.py and\npass the suggested --axes. Use --force "
            "to write the file anyway."
        )

    # Net camber sign check. A conventional aircraft section is cambered up,
    # so the mean camber ordinate should be positive. If it is not, the up
    # axis is inverted and every lift and pitching moment AVL gives you will
    # have the wrong sign.
    cam = np.array([float(np.mean(0.5 * (s["zu"] + s["zl"]))) for s in sections])
    print(f"  mean camber = {cam.mean():+.5f}")
    if cam.mean() < 0:
        print("  WARNING: net camber is negative. Your up axis is probably")
        print("           inverted. Re-run with a minus on the third axis,")
        print(f"           e.g. --axes {a.axes[:-1]}-{a.axes[-1]}")
    path = write_avl(sections, sref, b_half, a.out, a.name, a.mach, a.nchord, a.cspace)
    print(f"  wrote {path}")


if __name__ == "__main__":
    main()
