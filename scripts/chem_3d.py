#!/usr/bin/env python3
"""Organic Chemistry 3D Conformer, Stereochemistry & Quick 2D SVG Studio.

Complements Google's official Antigravity `chemistry` plugin with:
  - Fast stereochemistry / CIP (R)/(S) & (E)/(Z) / hybridization inspector
  - Offline built-in JEE molecule name -> SMILES dictionary + PubChem fallback
  - RDKit ETKDGv3 + MMFF94 3D conformer generator -> 100% offline, zero-CDN
    interactive rotatable HTML5 Canvas 3D viewer!
  - Quick standalone 2D SVG/PNG renderer with crisp stereo annotations & SMARTS
"""

from __future__ import annotations

import argparse
import html
import json
import urllib.parse
import urllib.request
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import AllChem, Descriptors, rdCIPLabeler, rdMolDescriptors
from rdkit.Chem.Draw import rdMolDraw2D

# Offline dictionary of common JEE/Organic Chemistry molecules so --name works offline instantaneously.
OFFLINE_NAME_TO_SMILES: dict[str, str] = {
    "methane": "C",
    "ethane": "CC",
    "propane": "CCC",
    "butane": "CCCC",
    "n-butane": "CCCC",
    "isobutane": "CC(C)C",
    "pentane": "CCCCC",
    "neopentane": "CC(C)(C)C",
    "hexane": "CCCCCC",
    "ethene": "C=C",
    "ethylene": "C=C",
    "propene": "CC=C",
    "propylene": "CC=C",
    "1-butene": "CCC=C",
    "2-butene": "CC=CC",
    "cis-2-butene": "C/C=C\\C",
    "(z)-2-butene": "C/C=C\\C",
    "trans-2-butene": "C/C=C/C",
    "(e)-2-butene": "C/C=C/C",
    "1,3-butadiene": "C=CC=C",
    "ethyne": "C#C",
    "acetylene": "C#C",
    "propyne": "CC#C",
    "cyclopropane": "C1CC1",
    "cyclobutane": "C1CCC1",
    "cyclopentane": "C1CCCC1",
    "cyclohexane": "C1CCCCC1",
    "methylcyclohexane": "CC1CCCCC1",
    "cis-1,4-dimethylcyclohexane": "C[C@H]1CC[C@@H](C)CC1",
    "trans-1,4-dimethylcyclohexane": "C[C@H]1CC[C@H](C)CC1",
    "cyclohexene": "C1CCC=CC1",
    "benzene": "c1ccccc1",
    "toluene": "Cc1ccccc1",
    "phenol": "Oc1ccccc1",
    "aniline": "Nc1ccccc1",
    "anisole": "COc1ccccc1",
    "benzaldehyde": "O=Cc1ccccc1",
    "benzoic acid": "O=C(O)c1ccccc1",
    "acetophenone": "CC(=O)c1ccccc1",
    "benzophenone": "O=C(c1ccccc1)c1ccccc1",
    "nitrobenzene": "O=[N+]([O-])c1ccccc1",
    "chlorobenzene": "Clc1ccccc1",
    "bromobenzene": "Brc1ccccc1",
    "styrene": "C=Cc1ccccc1",
    "salicylic acid": "O=C(O)c1ccccc1O",
    "aspirin": "CC(=O)Oc1ccccc1C(=O)O",
    "picric acid": "Oc1c([N+](=O)[O-])cc([N+](=O)[O-])cc1[N+](=O)[O-]",
    "o-xylene": "Cc1ccccc1C",
    "m-xylene": "Cc1cccc(C)c1",
    "p-xylene": "Cc1ccc(C)cc1",
    "naphthalene": "c1ccc2ccccc2c1",
    "anthracene": "c1ccc2cc3ccccc3cc2c1",
    "pyridine": "c1ccncc1",
    "pyrrole": "c1cc[nH]c1",
    "furan": "c1ccoc1",
    "thiophene": "c1ccsc1",
    "methanol": "CO",
    "ethanol": "CCO",
    "1-propanol": "CCCO",
    "2-propanol": "CC(O)C",
    "isopropanol": "CC(O)C",
    "tert-butanol": "CC(C)(C)O",
    "2-butanol": "CCC(O)C",
    "(r)-2-butanol": "CC[C@H](O)C",
    "(s)-2-butanol": "CC[C@@H](O)C",
    "ethylene glycol": "OCCO",
    "glycerol": "OCC(O)CO",
    "diethyl ether": "CCOCC",
    "thf": "C1CCOC1",
    "tetrahydrofuran": "C1CCOC1",
    "formaldehyde": "C=O",
    "acetaldehyde": "CC=O",
    "acetone": "CC(=O)C",
    "butanone": "CCC(=O)C",
    "cyclohexanone": "O=C1CCCCC1",
    "formic acid": "O=CO",
    "acetic acid": "CC(=O)O",
    "propanoic acid": "CCC(=O)O",
    "oxalic acid": "O=C(O)C(=O)O",
    "malonic acid": "O=C(O)CC(=O)O",
    "succinic acid": "O=C(O)CCC(=O)O",
    "lactic acid": "CC(O)C(=O)O",
    "(r)-lactic acid": "C[C@H](O)C(=O)O",
    "(s)-lactic acid": "C[C@@H](O)C(=O)O",
    "tartaric acid": "O=C(O)C(O)C(O)C(=O)O",
    "meso-tartaric acid": "O=C(O)[C@H](O)[C@@H](O)C(=O)O",
    "(2r,3r)-tartaric acid": "O=C(O)[C@H](O)[C@H](O)C(=O)O",
    "maleic acid": "O=C(O)/C=C\\C(=O)O",
    "fumaric acid": "O=C(O)/C=C/C(=O)O",
    "ethyl acetate": "CCOC(=O)C",
    "acetyl chloride": "CC(=O)Cl",
    "acetic anhydride": "CC(=O)OC(=O)C",
    "acetamide": "CC(=O)N",
    "urea": "NC(=O)N",
    "methylamine": "CN",
    "ethylamine": "CCN",
    "dimethylamine": "CNC",
    "trimethylamine": "CN(C)C",
    "acetonitrile": "CC#N",
    "chloroform": "ClC(Cl)Cl",
    "carbon tetrachloride": "ClC(Cl)(Cl)Cl",
    "dichloromethane": "ClCCl",
    "2-bromobutane": "CCC(Br)C",
    "(r)-2-bromobutane": "CC[C@H](Br)C",
    "(s)-2-bromobutane": "CC[C@@H](Br)C",
    "2-chlorobutane": "CCC(Cl)C",
    "tert-butyl bromide": "CC(C)(C)Br",
    "benzyl chloride": "ClCc1ccccc1",
    "glycine": "NCC(=O)O",
    "alanine": "CC(N)C(=O)O",
    "l-alanine": "C[C@H](N)C(=O)O",
    "d-glucose": "OC[C@H]1OC(O)[C@H](O)[C@@H](O)[C@@H]1O",
    "glucose": "OC[C@H]1OC(O)[C@H](O)[C@@H](O)[C@@H]1O",
    "caffeine": "Cn1c(=O)c2c(ncn2C)n(C)c1=O",
    "ibuprofen": "CC(C)Cc1ccc(C(C)C(=O)O)cc1",
    "paracetamol": "CC(=O)Nc1ccc(O)cc1",
}

_STEREO_MAP = {
    "STEREOE": "E",
    "STEREOZ": "Z",
    "STEREOCIS": "cis",
    "STEREOTRANS": "trans",
    "STEREOANY": "unspecified",
}


def resolve_smiles(value: str, is_name: bool) -> tuple[str, str]:
    """Return (smiles, source) where source is 'smiles', 'offline-dict', 'py2opsin', or 'pubchem'."""
    if not is_name:
        return value, "smiles"
    key = " ".join(value.strip().lower().split())
    if key in OFFLINE_NAME_TO_SMILES:
        return OFFLINE_NAME_TO_SMILES[key], "offline-dict"
    # Try 100% offline systematic IUPAC -> isomeric SMILES via py2opsin (JVM OPSIN)
    try:
        import py2opsin  # type: ignore

        opsin_smi = py2opsin.py2opsin(value.strip())
        if isinstance(opsin_smi, str) and opsin_smi.strip():
            return opsin_smi.strip(), "py2opsin"
    except Exception:
        pass
    q = urllib.parse.quote(value.strip())
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{q}/property/IsomericSMILES,CanonicalSMILES/JSON"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        props = data["PropertyTable"]["Properties"][0]
        smi = (
            props.get("IsomericSMILES")
            or props.get("SMILES")
            or props.get("CanonicalSMILES")
            or props.get("ConnectivitySMILES")
        )
        if smi:
            return smi, "pubchem"
    except Exception as exc:
        raise SystemExit(
            f"Could not resolve chemical name '{value}' offline or via PubChem ({exc}). "
            "Pass a SMILES string directly (without --name), or check network connectivity."
        )
    raise SystemExit(f"PubChem returned no SMILES for '{value}'. Pass SMILES directly.")


def build_mol(smiles: str) -> Chem.Mol:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise SystemExit(f"Invalid SMILES: {smiles}")
    Chem.AssignStereochemistry(mol, cleanIt=True, force=True, flagPossibleStereoCenters=True)
    try:
        rdCIPLabeler.AssignCIPLabels(mol)
    except Exception:
        pass
    return mol


def extract_stereo_info(mol: Chem.Mol) -> tuple[list[dict], list[dict], str]:
    chiral_raw = Chem.FindMolChiralCenters(mol, includeUnassigned=True, useLegacyImplementation=False)
    chiral_centers = []
    for idx, cip in chiral_raw:
        atom = mol.GetAtomWithIdx(idx)
        neighbors = [f"{n.GetSymbol()}{n.GetIdx()}" for n in atom.GetNeighbors()]
        chiral_centers.append({
            "atomIdx": idx,
            "symbol": atom.GetSymbol(),
            "atomLabel": f"{atom.GetSymbol()}{idx}",
            "cip": cip,
            "assigned": cip in ("R", "S"),
            "neighbors": neighbors,
        })

    double_bonds = []
    for b in mol.GetBonds():
        if b.GetBondType() == Chem.BondType.DOUBLE:
            stereo = str(b.GetStereo())
            cip_prop = b.GetProp("_CIPCode") if b.HasProp("_CIPCode") else None
            if stereo != "STEREONONE" or cip_prop:
                a1 = b.GetBeginAtom()
                a2 = b.GetEndAtom()
                cip_label = cip_prop or _STEREO_MAP.get(stereo, stereo)
                double_bonds.append({
                    "bondIdx": b.GetIdx(),
                    "atoms": [a1.GetIdx(), a2.GetIdx()],
                    "bondLabel": f"{a1.GetSymbol()}{a1.GetIdx()}={a2.GetSymbol()}{a2.GetIdx()}",
                    "stereo": stereo,
                    "cip": cip_label,
                })

    parts = []
    for c in chiral_centers:
        parts.append(f"{c['atomLabel']}: ({c['cip']})" if c["assigned"] else f"{c['atomLabel']}: (unassigned ?)")
    for db in double_bonds:
        parts.append(f"{db['bondLabel']}: ({db['cip']})")
    summary = "; ".join(parts) if parts else "Achiral / no stereogenic elements detected"
    return chiral_centers, double_bonds, summary


def cmd_inspect(args: argparse.Namespace) -> None:
    smiles, source = resolve_smiles(args.input, args.name)
    mol = build_mol(smiles)
    chiral_centers, double_bonds, stereo_summary = extract_stereo_info(mol)
    hybridization = [
        {
            "idx": a.GetIdx(),
            "symbol": a.GetSymbol(),
            "hybridization": str(a.GetHybridization()),
            "formalCharge": a.GetFormalCharge(),
            "inRing": a.IsInRing(),
            "aromatic": a.GetIsAromatic(),
        }
        for a in mol.GetAtoms()
    ]
    payload = {
        "input": args.input,
        "resolvedVia": source,
        "canonicalIsomericSmiles": Chem.MolToSmiles(mol, isomericSmiles=True),
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "molecularWeight": round(Descriptors.MolWt(mol), 3),
        "exactMass": round(Descriptors.ExactMolWt(mol), 4),
        "ringCount": rdMolDescriptors.CalcNumRings(mol),
        "aromaticRings": rdMolDescriptors.CalcNumAromaticRings(mol),
        "hbd": rdMolDescriptors.CalcNumHBD(mol),
        "hba": rdMolDescriptors.CalcNumHBA(mol),
        "rotatableBonds": rdMolDescriptors.CalcNumRotatableBonds(mol),
        "stereoSummary": stereo_summary,
        "unassignedStereoCenters": sum(1 for c in chiral_centers if not c["assigned"]),
        "chiralCenters": chiral_centers,
        "alkeneStereo": double_bonds,
        "heavyAtoms": hybridization,
    }
    print(json.dumps(payload, indent=2))


def cmd_render2d(args: argparse.Namespace) -> None:
    smiles, source = resolve_smiles(args.input, args.name)
    mol = build_mol(smiles)
    AllChem.Compute2DCoords(mol)
    highlight_atoms: list[int] = []
    highlight_bonds: list[int] = []
    if args.highlight_smarts:
        pat = Chem.MolFromSmarts(args.highlight_smarts)
        if pat is None:
            raise SystemExit(f"Invalid SMARTS: {args.highlight_smarts}")
        for match in mol.GetSubstructMatches(pat):
            highlight_atoms.extend(match)
            for b in pat.GetBonds():
                mb = mol.GetBondBetweenAtoms(match[b.GetBeginAtomIdx()], match[b.GetEndAtomIdx()])
                if mb is not None:
                    highlight_bonds.append(mb.GetIdx())

    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    is_png = out_path.suffix.lower() == ".png"
    drawer = rdMolDraw2D.MolDraw2DCairo(args.width, args.height) if is_png else rdMolDraw2D.MolDraw2DSVG(args.width, args.height)
    opts = drawer.drawOptions()
    opts.addStereoAnnotation = not args.no_stereo_labels
    opts.annotationFontScale = 0.85
    opts.bondLineWidth = 2.0
    opts.explicitMethyl = args.explicit_methyl
    opts.addAtomIndices = args.atom_indices
    if args.dark:
        rdMolDraw2D.SetDarkMode(opts)
    rdMolDraw2D.PrepareAndDrawMolecule(
        drawer,
        mol,
        legend=args.legend or "",
        highlightAtoms=sorted(set(highlight_atoms)),
        highlightBonds=sorted(set(highlight_bonds)),
    )
    drawer.FinishDrawing()
    if is_png:
        drawer.WriteDrawingText(str(out_path))
    else:
        out_path.write_text(drawer.GetDrawingText(), encoding="utf-8")
    _, _, stereo_summary = extract_stereo_info(mol)
    print(json.dumps({
        "output": str(out_path),
        "resolvedVia": source,
        "smiles": Chem.MolToSmiles(mol, isomericSmiles=True),
        "stereoSummary": stereo_summary,
    }, indent=2))


def generate_conformers(mol: Chem.Mol, num_confs: int) -> list[dict]:
    mol_h = Chem.AddHs(mol)
    params = AllChem.ETKDGv3()
    params.randomSeed = 42
    params.pruneRmsThresh = 0.35
    # Generate extra candidates so RMS pruning + MMFF94 ranking surfaces distinct low-energy conformers.
    target = max(1, num_confs)
    candidates = max(target * 4, 12) if target > 1 else 1
    cids = list(AllChem.EmbedMultipleConfs(mol_h, numConfs=candidates, params=params))
    if not cids:
        res = AllChem.EmbedMolecule(mol_h, randomSeed=42)
        if res == -1:
            res = AllChem.EmbedMolecule(mol_h, randomSeed=42, useRandomCoords=True)
        cids = [0] if res != -1 else []
    if not cids:
        AllChem.Compute2DCoords(mol_h)
        return [{
            "id": 0,
            "rank": 1,
            "energy": None,
            "relEnergy": 0.0,
            "sdf": Chem.MolToMolBlock(mol_h),
        }]

    confs = []
    props = AllChem.MMFFGetMoleculeProperties(mol_h)
    for cid in cids:
        energy = None
        if props is not None:
            ff = AllChem.MMFFGetMoleculeForceField(mol_h, props, confId=cid)
            if ff is not None:
                ff.Minimize(maxIts=500)
                energy = ff.CalcEnergy()
        if energy is None:
            ff = AllChem.UFFGetMoleculeForceField(mol_h, confId=cid)
            if ff is not None:
                ff.Minimize(maxIts=500)
                energy = ff.CalcEnergy()
        confs.append({
            "id": cid,
            "energy": energy,
            "sdf": Chem.MolToMolBlock(mol_h, confId=cid),
        })

    confs.sort(key=lambda c: c["energy"] if c["energy"] is not None else 1e9)
    # Deduplicate near-identical energies if excess candidates collapsed to the same minimum.
    deduped = []
    for c in confs:
        if deduped and c["energy"] is not None and deduped[-1]["energy"] is not None:
            if abs(c["energy"] - deduped[-1]["energy"]) < 0.02 and len(confs) > target:
                continue
        deduped.append(c)
        if len(deduped) >= target:
            break
    if not deduped:
        deduped = confs[:target]

    base = deduped[0]["energy"]
    for i, c in enumerate(deduped, start=1):
        c["rank"] = i
        c["relEnergy"] = round(c["energy"] - base, 2) if (c["energy"] is not None and base is not None) else None
    return deduped


def cmd_render3d(args: argparse.Namespace) -> None:
    smiles, source = resolve_smiles(args.input, args.name)
    mol = build_mol(smiles)
    chiral_centers, double_bonds, stereo_summary = extract_stereo_info(mol)
    confs = generate_conformers(mol, args.conformers)
    AllChem.Compute2DCoords(mol)
    drawer = rdMolDraw2D.MolDraw2DSVG(360, 240)
    opts = drawer.drawOptions()
    opts.addStereoAnnotation = True
    opts.annotationFontScale = 0.85
    opts.bondLineWidth = 2.0
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
    drawer.FinishDrawing()
    svg_2d = drawer.GetDrawingText()

    title = args.title or args.input
    formula = rdMolDescriptors.CalcMolFormula(mol)
    can_smiles = Chem.MolToSmiles(mol, isomericSmiles=True)

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{html.escape(title)} — 3D Organic Lab</title>
<style>
  :root {{ --bg:#0d1016; --panel:#151a24; --border:#262e3f; --text:#e8edf5; --muted:#94a3b8; --accent:#60a5fa; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Inter",sans-serif; background:var(--bg); color:var(--text); display:grid; grid-template-rows:auto 1fr; height:100vh; }}
  header {{ display:flex; align-items:center; gap:10px; padding:12px 16px; background:var(--panel); border-bottom:1px solid var(--border); flex-wrap:wrap; }}
  header h1 {{ margin:0; font-size:15px; font-weight:600; }}
  .pill {{ font-size:12px; padding:3px 9px; border-radius:999px; border:1px solid var(--border); background:#1d2433; color:var(--muted); }}
  .pill.stereo {{ color:#fde68a; border-color:#524420; background:#241e10; }}
  button {{ background:#1d2433; color:var(--text); border:1px solid var(--border); border-radius:6px; padding:5px 10px; font-size:12px; cursor:pointer; }}
  button.active, button:hover {{ border-color:var(--accent); color:#fff; }}
  main {{ display:grid; grid-template-columns:1fr 340px; min-height:0; }}
  #viewerWrap {{ position:relative; width:100%; height:100%; background:#0b0e14; overflow:hidden; }}
  #viewer, #offlineCanvas {{ width:100%; height:100%; position:absolute; inset:0; }}
  #offlineCanvas {{ display:none; cursor:grab; }}
  #offlineCanvas:active {{ cursor:grabbing; }}
  #engineBadge {{ position:absolute; left:12px; bottom:12px; z-index:10; font-size:11px; color:var(--muted); background:rgba(21,26,36,0.85); border:1px solid var(--border); padding:4px 8px; border-radius:6px; }}
  aside {{ border-left:1px solid var(--border); background:var(--panel); padding:14px; overflow-y:auto; display:flex; flex-direction:column; gap:12px; }}
  .card {{ background:#111620; border:1px solid var(--border); border-radius:10px; padding:12px; }}
  .svg-well {{ background:#ffffff; border-radius:8px; padding:6px; display:flex; justify-content:center; }}
  .svg-well svg {{ max-width:100%; height:auto; }}
  .label {{ font-size:11px; text-transform:uppercase; letter-spacing:0.05em; color:var(--muted); margin-bottom:6px; }}
  .btn-group {{ display:flex; flex-wrap:wrap; gap:6px; }}
  code {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11px; word-break:break-all; color:#bfdbfe; }}
</style>
</head>
<body>
<header>
  <h1>{html.escape(title)}</h1>
  <span class="pill">{html.escape(formula)}</span>
  <span class="pill stereo">Stereo: {html.escape(stereo_summary)}</span>
  <div class="btn-group" style="margin-left:auto">
    <button data-style="ballstick" class="active">Ball &amp; Stick</button>
    <button data-style="stick">Stick</button>
    <button data-style="sphere">Spacefill (vdW)</button>
    <button id="labelsBtn">Labels</button>
    <button id="spinBtn">Spin</button>
    <button id="resetBtn">Reset</button>
  </div>
</header>
<main>
  <div id="viewerWrap">
    <div id="viewer"></div>
    <canvas id="offlineCanvas"></canvas>
    <div id="engineBadge">Initializing 3D viewer...</div>
  </div>
  <aside>
    <div class="card">
      <div class="label">2D Skeletal Reference (RDKit Stereo)</div>
      <div class="svg-well">{svg_2d}</div>
    </div>
    <div class="card">
      <div class="label">3D Conformers (MMFF94 Relative Energy)</div>
      <div class="btn-group" id="confBtns"></div>
    </div>
    <div class="card">
      <div class="label">Verified Isomeric SMILES</div>
      <code>{html.escape(can_smiles)}</code>
    </div>
    <p style="font-size:12px;color:var(--muted);line-height:1.5;margin:0">
      Drag to rotate in 3D, scroll to zoom. Inspect axial vs. equatorial hydrogens, dihedral angles (Newman projection along any C–C bond), and steric clashes directly. Works 100% offline via built-in Canvas 3D fallback.
    </p>
  </aside>
</main>
<script>
const CONFS = {json.dumps(confs)};
const STEREO_CHIRAL = {json.dumps(chiral_centers)};
let styleMode = "ballstick";
let spinning = false;
let showLabels = false;
let currentIdx = 0;
let viewer = null;
let offlineMode = false;

// --- Zero-dependency Offline 3D SDF Canvas Renderer ---
const CPK = {{
  H:"#e2e8f0", C:"#64748b", N:"#3b82f6", O:"#ef4444", F:"#22c55e",
  Cl:"#16a34a", Br:"#b45309", I:"#9333ea", S:"#eab308", P:"#f97316"
}};
const RADII = {{ H:0.32, C:0.76, N:0.71, O:0.66, F:0.57, Cl:0.99, Br:1.14, I:1.33, S:1.05, P:1.07 }};
let mol3d = {{ atoms: [], bonds: [] }};
let rotX = 0.4, rotY = 0.6, zoom = 1.0;
let dragging = false, lastX = 0, lastY = 0;

function parseSdf(sdf) {{
  const lines = sdf.split(/\\r?\\n/);
  if (lines.length < 5) return {{ atoms: [], bonds: [] }};
  const counts = lines[3];
  const nAtoms = parseInt(counts.slice(0, 3).trim(), 10) || 0;
  const nBonds = parseInt(counts.slice(3, 6).trim(), 10) || 0;
  const atoms = [];
  let cx = 0, cy = 0, cz = 0;
  for (let i = 0; i < nAtoms; i++) {{
    const l = lines[4 + i] || "";
    const x = parseFloat(l.slice(0, 10));
    const y = parseFloat(l.slice(10, 20));
    const z = parseFloat(l.slice(20, 30));
    const el = l.slice(31, 34).trim() || "C";
    atoms.push({{ idx: i, x, y, z, el }});
    cx += x; cy += y; cz += z;
  }}
  if (nAtoms > 0) {{
    cx /= nAtoms; cy /= nAtoms; cz /= nAtoms;
    atoms.forEach(a => {{ a.x -= cx; a.y -= cy; a.z -= cz; }});
  }}
  const bonds = [];
  for (let i = 0; i < nBonds; i++) {{
    const l = lines[4 + nAtoms + i] || "";
    const a1 = parseInt(l.slice(0, 3).trim(), 10) - 1;
    const a2 = parseInt(l.slice(3, 6).trim(), 10) - 1;
    const order = parseInt(l.slice(6, 9).trim(), 10) || 1;
    if (a1 >= 0 && a2 >= 0 && a1 < nAtoms && a2 < nAtoms) bonds.push({{ a1, a2, order }});
  }}
  return {{ atoms, bonds }};
}}

function drawOffline() {{
  if (!offlineMode) return;
  const canvas = document.getElementById("offlineCanvas");
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(rect.width * dpr) || canvas.height !== Math.round(rect.height * dpr)) {{
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
  }}
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#0b0e14";
  ctx.fillRect(0, 0, rect.width, rect.height);

  const cosX = Math.cos(rotX), sinX = Math.sin(rotX);
  const cosY = Math.cos(rotY), sinY = Math.sin(rotY);
  const maxR = Math.max(1.5, ...mol3d.atoms.map(a => Math.hypot(a.x, a.y, a.z)));
  const scale = (Math.min(rect.width, rect.height) * 0.34 / maxR) * zoom;
  const ox = rect.width / 2, oy = rect.height / 2;

  const proj = mol3d.atoms.map(a => {{
    const x1 = a.x * cosY + a.z * sinY;
    const z1 = -a.x * sinY + a.z * cosY;
    const y2 = a.y * cosX - z1 * sinX;
    const z2 = a.y * sinX + z1 * cosX;
    const persp = 14 / (14 - z2 * 0.6);
    return {{ ...a, sx: ox + x1 * scale * persp, sy: oy - y2 * scale * persp, z: z2, persp }};
  }});

  const items = [];
  mol3d.bonds.forEach(b => {{
    const p1 = proj[b.a1], p2 = proj[b.a2];
    items.push({{ kind: "bond", z: (p1.z + p2.z) / 2, p1, p2, order: b.order }});
  }});
  proj.forEach(p => items.push({{ kind: "atom", z: p.z, p }}));
  items.sort((a, b) => a.z - b.z);

  const chiralMap = Object.fromEntries(STEREO_CHIRAL.map(c => [c.atomIdx, c.cip]));
  for (const it of items) {{
    if (it.kind === "bond" && styleMode !== "sphere") {{
      const dx = it.p2.sx - it.p1.sx, dy = it.p2.sy - it.p1.sy;
      const len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len, ny = dx / len;
      const offsets = it.order === 2 ? [-2.5, 2.5] : it.order === 3 ? [-4, 0, 4] : [0];
      ctx.strokeStyle = "#94a3b8";
      ctx.lineCap = "round";
      ctx.lineWidth = (styleMode === "stick" ? 5 : 3.2) * ((it.p1.persp + it.p2.persp) / 2);
      for (const off of offsets) {{
        ctx.beginPath();
        ctx.moveTo(it.p1.sx + nx * off, it.p1.sy + ny * off);
        ctx.lineTo(it.p2.sx + nx * off, it.p2.sy + ny * off);
        ctx.stroke();
      }}
    }} else if (it.kind === "atom") {{
      const p = it.p;
      const baseR = (RADII[p.el] || 0.72) * scale * p.persp;
      const r = styleMode === "sphere" ? baseR * 1.15 : styleMode === "stick" ? Math.max(3, baseR * 0.16) : Math.max(5, baseR * 0.34);
      const col = CPK[p.el] || "#38bdf8";
      const grad = ctx.createRadialGradient(p.sx - r * 0.32, p.sy - r * 0.32, r * 0.1, p.sx, p.sy, r);
      grad.addColorStop(0, "#ffffff");
      grad.addColorStop(0.35, col);
      grad.addColorStop(1, "#0f172a");
      ctx.beginPath();
      ctx.arc(p.sx, p.sy, r, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();
      if (showLabels || chiralMap[p.idx]) {{
        const tag = chiralMap[p.idx] ? `${{p.el}}${{p.idx}} (${{chiralMap[p.idx]}})` : (p.el !== "H" ? `${{p.el}}${{p.idx}}` : "");
        if (tag) {{
          ctx.font = "600 11px -apple-system, sans-serif";
          ctx.fillStyle = chiralMap[p.idx] ? "#fde68a" : "#e2e8f0";
          ctx.fillText(tag, p.sx + r + 3, p.sy + 4);
        }}
      }}
    }}
  }}
}}

function applyStyle() {{
  if (offlineMode) {{ drawOffline(); return; }}
  if (!viewer) return;
  viewer.setStyle({{}}, {{}});
  if (styleMode === "ballstick") {{
    viewer.setStyle({{}}, {{ stick: {{ radius: 0.16, colorscheme: "Jmol" }}, sphere: {{ scale: 0.28, colorscheme: "Jmol" }} }});
  }} else if (styleMode === "stick") {{
    viewer.setStyle({{}}, {{ stick: {{ radius: 0.18, colorscheme: "Jmol" }} }});
  }} else {{
    viewer.setStyle({{}}, {{ sphere: {{ colorscheme: "Jmol" }} }});
  }}
  viewer.removeAllLabels();
  if (showLabels) {{
    const atoms = viewer.selectedAtoms({{}});
    const chiralMap = Object.fromEntries(STEREO_CHIRAL.map(c => [c.atomIdx, c.cip]));
    atoms.forEach((a, i) => {{
      if (a.elem !== "H" || chiralMap[i]) {{
        const txt = chiralMap[i] ? `${{a.elem}}${{i}} (${{chiralMap[i]}})` : `${{a.elem}}${{i}}`;
        viewer.addLabel(txt, {{ position: a, fontSize: 11, fontColor: chiralMap[i] ? "#fde68a" : "#e2e8f0", backgroundColor: "rgba(15,23,42,0.75)", borderThickness: 0 }});
      }}
    }});
  }}
  viewer.render();
}}

function loadConf(idx) {{
  currentIdx = idx;
  document.querySelectorAll("#confBtns button").forEach((b, i) => b.classList.toggle("active", i === idx));
  if (offlineMode) {{
    mol3d = parseSdf(CONFS[idx].sdf);
    drawOffline();
    return;
  }}
  if (!viewer) return;
  viewer.removeAllModels();
  viewer.addModel(CONFS[idx].sdf, "sdf");
  applyStyle();
  viewer.zoomTo();
  viewer.render();
}}

window.addEventListener("DOMContentLoaded", () => {{
  const confBox = document.getElementById("confBtns");
  CONFS.forEach((c, i) => {{
    const btn = document.createElement("button");
    const eLabel = c.relEnergy !== null ? ` (+${{c.relEnergy}} kcal/mol)` : "";
    btn.textContent = `Conf ${{c.rank}}${{i === 0 ? " · Lowest" : eLabel}}`;
    btn.addEventListener("click", () => loadConf(i));
    confBox.appendChild(btn);
  }});
  document.querySelectorAll("button[data-style]").forEach(btn => {{
    btn.addEventListener("click", () => {{
      styleMode = btn.dataset.style;
      document.querySelectorAll("button[data-style]").forEach(b => b.classList.toggle("active", b === btn));
      applyStyle();
    }});
  }});
  document.getElementById("labelsBtn").addEventListener("click", (e) => {{
    showLabels = !showLabels;
    e.currentTarget.classList.toggle("active", showLabels);
    applyStyle();
  }});

  const badge = document.getElementById("engineBadge");
  if (typeof $3Dmol !== "undefined") {{
    viewer = $3Dmol.createViewer("viewer", {{ backgroundColor: "#0b0e14" }});
    badge.textContent = "WebGL · 3Dmol.js";
    loadConf(0);
    document.getElementById("spinBtn").addEventListener("click", (e) => {{
      spinning = !spinning;
      e.currentTarget.classList.toggle("active", spinning);
      viewer.spin(spinning ? "y" : false);
    }});
    document.getElementById("resetBtn").addEventListener("click", () => {{
      viewer.zoomTo();
      viewer.render();
    }});
  }} else {{
    offlineMode = true;
    document.getElementById("viewer").style.display = "none";
    const canvas = document.getElementById("offlineCanvas");
    canvas.style.display = "block";
    badge.textContent = "Offline Canvas 3D · Drag to Rotate";
    loadConf(0);
    canvas.addEventListener("pointerdown", e => {{ dragging = true; lastX = e.clientX; lastY = e.clientY; }});
    window.addEventListener("pointerup", () => {{ dragging = false; }});
    window.addEventListener("pointermove", e => {{
      if (!dragging) return;
      rotY += (e.clientX - lastX) * 0.012;
      rotX += (e.clientY - lastY) * 0.012;
      lastX = e.clientX; lastY = e.clientY;
      drawOffline();
    }});
    canvas.addEventListener("wheel", e => {{
      e.preventDefault();
      zoom = Math.max(0.4, Math.min(3.0, zoom * (e.deltaY > 0 ? 0.92 : 1.08)));
      drawOffline();
    }}, {{ passive: false }});
    window.addEventListener("resize", drawOffline);
    document.getElementById("spinBtn").addEventListener("click", (e) => {{
      spinning = !spinning;
      e.currentTarget.classList.toggle("active", spinning);
    }});
    document.getElementById("resetBtn").addEventListener("click", () => {{
      rotX = 0.4; rotY = 0.6; zoom = 1.0;
      drawOffline();
    }});
    (function tick() {{
      if (spinning && !dragging) {{ rotY += 0.015; drawOffline(); }}
      requestAnimationFrame(tick);
    }})();
  }}
}});
</script>
</body>
</html>
"""
    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    print(json.dumps({
        "output": str(out_path),
        "resolvedVia": source,
        "smiles": can_smiles,
        "formula": formula,
        "stereoSummary": stereo_summary,
        "chiralCenters": chiral_centers,
        "alkeneStereo": double_bonds,
        "conformers": [{"rank": c["rank"], "relEnergyKcalMol": c["relEnergy"]} for c in confs],
    }, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Organic Chemistry 3D Conformer & Stereochemistry Studio")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("inspect", help="Verify SMILES/name, formula, CIP (R)/(S), (E)/(Z), and hybridization")
    p1.add_argument("input")
    p1.add_argument("--name", action="store_true", help="Resolve chemical name (offline JEE dict + PubChem fallback)")
    p1.set_defaults(func=cmd_inspect)

    p2 = sub.add_parser("render2d", help="Render standalone 2D SVG/PNG structure with crisp stereo & SMARTS highlighting")
    p2.add_argument("input")
    p2.add_argument("--name", action="store_true")
    p2.add_argument("--out", required=True)
    p2.add_argument("--legend", default="")
    p2.add_argument("--highlight-smarts")
    p2.add_argument("--width", type=int, default=420)
    p2.add_argument("--height", type=int, default=280)
    p2.add_argument("--dark", action="store_true")
    p2.add_argument("--explicit-methyl", action="store_true")
    p2.add_argument("--atom-indices", action="store_true", help="Show atom indices for CIP/numbering drills")
    p2.add_argument("--no-stereo-labels", action="store_true")
    p2.set_defaults(func=cmd_render2d)

    p3 = sub.add_parser("render3d", help="Generate MMFF94 3D conformers + interactive 3Dmol.js/offline Canvas 3D HTML viewer")
    p3.add_argument("input")
    p3.add_argument("--name", action="store_true")
    p3.add_argument("--title")
    p3.add_argument("--conformers", type=int, default=3)
    p3.add_argument("--out", required=True)
    p3.set_defaults(func=cmd_render3d)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
