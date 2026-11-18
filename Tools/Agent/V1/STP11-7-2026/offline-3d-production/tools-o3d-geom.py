"""Geometry engine for the offline-3d-production Skill Tool.

Pure standard-library 3D mesh maths: OBJ/PLY/STL import, OBJ/STL/GLB export,
topology validation, repair, decimation, voxel remesh, subdivision, sampling,
self-intersection screening and UV analysis. No third-party imports, so this
module also runs under Blender's bundled Python.

Conventions
-----------
* Coordinates are right-handed metres unless a command says otherwise.
* ``Mesh.verts`` is a list of ``[x, y, z]`` lists; ``Mesh.faces`` is a list of
  index lists (triangles preferred, n-gons are accepted and triangulated on
  demand through :meth:`Mesh.tri_faces`).
* Every operation is deterministic: no set iteration order leaks into output,
  and RNG use is always seeded explicitly.
"""

from __future__ import annotations

import json
import math
import os
import random
import struct
import sys

core = sys.modules["o3d_core"]
EPS = 1e-9

# Hard ceilings so a malformed file cannot exhaust an air-gapped workstation.
MAX_TRIS = 800000
MAX_VERTS = 800000


# ---------------------------------------------------------------------------
# Mesh container
# ---------------------------------------------------------------------------


class Mesh:
    def __init__(self, verts=None, faces=None, uvs=None, face_uvs=None, name="mesh", source=None):
        self.verts = verts or []
        self.faces = faces or []
        self.uvs = uvs or []
        self.face_uvs = face_uvs or []
        self.name = name
        self.source = source or {}
        self._tri_cache = None

    # -- basic measures ----------------------------------------------------
    def clone(self):
        return Mesh(
            [list(v) for v in self.verts],
            [list(f) for f in self.faces],
            [list(t) for t in self.uvs],
            [list(t) for t in self.face_uvs],
            self.name,
            dict(self.source),
        )

    def __len__(self):
        return len(self.faces)

    @property
    def tri_count(self):
        return sum(max(0, len(f) - 2) for f in self.faces)

    def bounds(self):
        if not self.verts:
            return [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
        lo = [min(v[i] for v in self.verts) for i in range(3)]
        hi = [max(v[i] for v in self.verts) for i in range(3)]
        return [lo, hi]

    def dimensions(self):
        lo, hi = self.bounds()
        return [hi[i] - lo[i] for i in range(3)]

    def center(self):
        lo, hi = self.bounds()
        return [(lo[i] + hi[i]) / 2.0 for i in range(3)]

    def surface_area(self):
        total = 0.0
        for tri in self.tri_faces():
            total += tri_area(self.verts[tri[0]], self.verts[tri[1]], self.verts[tri[2]])
        return total

    def signed_volume(self):
        """Divergence-theorem volume; negative means inverted winding overall."""
        total = 0.0
        for tri in self.tri_faces():
            a, b, c = self.verts[tri[0]], self.verts[tri[1]], self.verts[tri[2]]
            total += (
                a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            ) / 6.0
        return total

    def tri_faces(self):
        if self._tri_cache is None:
            out = []
            for f in self.faces:
                if len(f) == 3:
                    out.append(f)
                elif len(f) > 3:
                    for i in range(1, len(f) - 1):
                        out.append([f[0], f[i], f[i + 1]])
            self._tri_cache = out
        return self._tri_cache

    def invalidate(self):
        self._tri_cache = None

    # -- transform ---------------------------------------------------------
    def transform(self, matrix_rows):
        """Apply a 4x4 row-major affine matrix (list of 4 rows of 4)."""
        m = matrix_rows
        out = []
        for x, y, z in self.verts:
            out.append(
                [
                    m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
                    m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
                    m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3],
                ]
            )
        self.verts = out
        self.invalidate()
        return self

    def scale(self, factor):
        if isinstance(factor, (int, float)):
            factor = [float(factor)] * 3
        self.verts = [[v[0] * factor[0], v[1] * factor[1], v[2] * factor[2]] for v in self.verts]
        self.invalidate()
        return self

    def translate(self, offset):
        self.verts = [[v[0] + offset[0], v[1] + offset[1], v[2] + offset[2]] for v in self.verts]
        self.invalidate()
        return self


# ---------------------------------------------------------------------------
# Vector maths
# ---------------------------------------------------------------------------


def sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def add(a, b):
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def length(a):
    return math.sqrt(dot(a, a))


def normalize(a, fallback=(0.0, 0.0, 1.0)):
    n = length(a)
    if n < EPS:
        return list(fallback)
    return [a[0] / n, a[1] / n, a[2] / n]


def tri_area(a, b, c):
    return 0.5 * length(cross(sub(b, a), sub(c, a)))


def tri_normal(a, b, c):
    n = cross(sub(b, a), sub(c, a))
    ln = length(n)
    if ln < EPS:
        return [0.0, 0.0, 0.0]
    return [n[0] / ln, n[1] / ln, n[2] / ln]


# ---------------------------------------------------------------------------
# Importers
# ---------------------------------------------------------------------------


def load_mesh(path, name=None):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".obj":
        mesh = load_obj(path)
    elif ext == ".ply":
        mesh = load_ply(path)
    elif ext == ".stl":
        mesh = load_stl(path)
    elif ext == ".json":
        mesh = load_mesh_json(path)
    elif ext == ".gltf" or ext == ".glb":
        mesh = load_gltf(path)
    else:
        raise core.invalid(
            "Unsupported mesh format '{0}'.".format(ext or "(none)"),
            "Supported inputs: .obj, .ply, .stl, .gltf, .glb, and the tool's own .json mesh format.",
        )
    if name:
        mesh.name = name
    mesh.source["path"] = os.path.basename(path)
    mesh.source["format"] = ext.lstrip(".")
    mesh.source["bytes"] = os.path.getsize(path)
    mesh.source["sha256"] = core.sha256_file(path)
    return mesh


def load_obj(path):
    text = core.read_text(path)
    verts, faces, uvs, face_uvs = [], [], [], []
    nvert = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] == "#":
            continue
        if line.startswith("v "):
            parts = line[2:].split()
            if len(parts) < 3:
                continue
            verts.append([float(parts[0]), float(parts[1]), float(parts[2])])
            nvert += 1
        elif line.startswith("vt "):
            parts = line[2:].split()
            if len(parts) >= 2:
                uvs.append([float(parts[0]), float(parts[1])])
        elif line.startswith("f "):
            idx, uidx = [], []
            for token in line[2:].split():
                bits = token.split("/")
                try:
                    i = int(bits[0])
                except ValueError:
                    continue
                idx.append(i - 1 if i > 0 else len(verts) + i)
                if len(bits) > 1 and bits[1]:
                    j = int(bits[1])
                    uidx.append(j - 1 if j > 0 else len(uvs) + j)
            if len(idx) >= 3:
                faces.append(idx)
                face_uvs.append(uidx if len(uidx) == len(idx) else [])
    if not verts:
        raise core.invalid("OBJ contains no vertices: {0}".format(os.path.basename(path)))
    mesh = Mesh(verts, faces, uvs, face_uvs, name=os.path.splitext(os.path.basename(path))[0])
    check_mesh_size(mesh)
    return mesh


def load_ply(path):
    data = core.read_bytes(path)
    if not data.startswith(b"ply"):
        raise core.invalid("Not a PLY file (missing magic header).")
    end = data.find(b"end_header")
    if end < 0:
        raise core.invalid("PLY header is not terminated by end_header.")
    header = data[:end].decode("ascii", "replace").splitlines()
    body_start = data.find(b"\n", end) + 1
    fmt, nvert, nface = None, 0, 0
    props = []
    in_vertex = False
    for line in header:
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "format":
            fmt = parts[1]
        elif parts[0] == "element":
            in_vertex = parts[1] == "vertex"
            if in_vertex:
                nvert = int(parts[2])
            elif parts[1] == "face":
                nface = int(parts[2])
        elif parts[0] == "property" and in_vertex:
            props.append(parts[-1])
    if fmt not in ("ascii", "binary_little_endian"):
        raise core.invalid(
            "PLY format '{0}' is not supported.".format(fmt),
            "Re-export as ascii or binary_little_endian PLY, or convert to OBJ.",
        )
    verts = []
    if fmt == "ascii":
        tokens = data[body_start:].split()
        pos = 0
        pcount = len(props)
        try:
            xyz = [props.index("x"), props.index("y"), props.index("z")]
        except ValueError:
            raise core.invalid("PLY vertex element must declare x, y and z properties.")
        for _ in range(nvert):
            row = tokens[pos : pos + pcount]
            pos += pcount
            verts.append([float(row[xyz[0]]), float(row[xyz[1]]), float(row[xyz[2]])])
        faces = []
        for _ in range(nface):
            n = int(tokens[pos])
            pos += 1
            faces.append([int(t) for t in tokens[pos : pos + n]])
            pos += n
    else:
        types = {"float": "<f", "double": "<d", "float32": "<f", "float64": "<d"}
        stride = 4 * len(props)
        xyz = [props.index("x"), props.index("y"), props.index("z")]
        for i in range(nvert):
            base = body_start + i * stride
            row = struct.unpack_from("<" + "f" * len(props), data, base)
            verts.append([float(row[xyz[0]]), float(row[xyz[1]]), float(row[xyz[2]])])
        faces = []
        pos = body_start + nvert * stride
        for _ in range(nface):
            n = data[pos]
            pos += 1
            idx = list(data[pos : pos + n])
            pos += n
            faces.append(idx)
    mesh = Mesh(verts, faces, name=os.path.splitext(os.path.basename(path))[0])
    check_mesh_size(mesh)
    return mesh


def load_stl(path):
    data = core.read_bytes(path)
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        if 84 + n * 50 == len(data):
            verts, faces, index = [], [], {}
            for i in range(n):
                off = 84 + i * 50 + 12
                tri = []
                for k in range(3):
                    xyz = struct.unpack_from("<fff", data, off + k * 12)
                    key = (round(xyz[0], 6), round(xyz[1], 6), round(xyz[2], 6))
                    if key not in index:
                        index[key] = len(verts)
                        verts.append([float(xyz[0]), float(xyz[1]), float(xyz[2])])
                    tri.append(index[key])
                faces.append(tri)
            mesh = Mesh(verts, faces, name=os.path.splitext(os.path.basename(path))[0])
            check_mesh_size(mesh)
            return mesh
    text = core.read_text(path)
    if "facet" not in text.lower():
        raise core.invalid("STL file is neither binary nor ASCII STL.")
    verts, faces, index, current = [], [], {}, []

    def addv(xyz):
        key = (round(xyz[0], 6), round(xyz[1], 6), round(xyz[2], 6))
        if key not in index:
            index[key] = len(verts)
            verts.append([float(xyz[0]), float(xyz[1]), float(xyz[2])])
        return index[key]

    for line in text.splitlines():
        s = line.strip()
        if s.lower().startswith("vertex"):
            parts = s.split()
            current.append(addv([float(parts[1]), float(parts[2]), float(parts[3])]))
        elif s.lower().startswith("endfacet"):
            if len(current) == 3:
                faces.append(current)
            current = []
    mesh = Mesh(verts, faces, name=os.path.splitext(os.path.basename(path))[0])
    check_mesh_size(mesh)
    return mesh


def load_mesh_json(path):
    data = core.load_json(path, "mesh JSON")
    if not isinstance(data, dict) or "verts" not in data or "faces" not in data:
        raise core.invalid("Mesh JSON must contain 'verts' and 'faces' arrays.")
    mesh = Mesh(
        [[float(c) for c in v[:3]] for v in data["verts"]],
        [[int(i) for i in f] for f in data["faces"]],
        data.get("uvs") or [],
        data.get("face_uvs") or [],
        name=data.get("name") or os.path.splitext(os.path.basename(path))[0],
    )
    check_mesh_size(mesh)
    return mesh


def load_gltf(path):
    data = core.read_bytes(path)
    base_dir = os.path.dirname(path)
    if path.lower().endswith(".glb"):
        if data[:4] != b"glTF":
            raise core.invalid("GLB file is missing the 'glTF' magic header.")
        version, total = struct.unpack_from("<II", data, 4)
        if version != 2:
            raise core.invalid("Only glTF 2.0 is supported (found version {0}).".format(version))
        pos = 12
        gltf, bin_chunk = None, None
        while pos + 8 <= len(data):
            clen, ctype = struct.unpack_from("<II", data, pos)
            chunk = data[pos + 8 : pos + 8 + clen]
            if ctype == 0x4E4F534A:
                gltf = json.loads(chunk.decode("utf-8").rstrip("\x00 "))
            elif ctype == 0x004E4942:
                bin_chunk = chunk
            pos += 8 + clen
        if gltf is None:
            raise core.invalid("GLB contains no JSON chunk.")
    else:
        gltf = core.load_json(path, "glTF file")
        bin_chunk = None
        for buf in gltf.get("buffers", []):
            uri = buf.get("uri", "")
            if uri.startswith("data:"):
                import base64

                bin_chunk = base64.b64decode(uri.split(",", 1)[1])
                break
            if uri and not uri.startswith("data:"):
                ref = os.path.join(base_dir, uri.replace("/", os.sep))
                if os.path.exists(ref):
                    bin_chunk = core.read_bytes(ref)
                    break
    return gltf_to_mesh(gltf, bin_chunk, os.path.splitext(os.path.basename(path))[0])


def gltf_to_mesh(gltf, bin_chunk, name="mesh"):
    if bin_chunk is None:
        raise core.invalid(
            "glTF has no readable binary buffer.",
            "Export a .glb or embed the buffer as a data URI; external buffer files must sit beside the .gltf.",
        )
    comp = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
    ncomp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
    views = gltf.get("bufferViews", [])
    accessors = gltf.get("accessors", [])

    def read_accessor(idx):
        acc = accessors[idx]
        view = views[acc["bufferView"]]
        fmt, size = comp[acc["componentType"]]
        count = ncomp[acc["type"]]
        offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = view.get("byteStride") or size * count
        out = []
        for i in range(acc["count"]):
            vals = struct.unpack_from("<" + fmt * count, bin_chunk, offset + i * stride)
            out.append(list(vals))
        return out

    verts, faces, uvs, face_uvs = [], [], [], []
    prim_uv_offset = 0
    for mesh_def in gltf.get("meshes", []):
        for prim in mesh_def.get("primitives", []):
            if prim.get("mode", 4) != 4:
                continue
            pos = read_accessor(prim["attributes"]["POSITION"])
            base = len(verts)
            verts.extend([[float(c) for c in p[:3]] for p in pos])
            uv_list = None
            if "TEXCOORD_0" in prim["attributes"]:
                uv_list = read_accessor(prim["attributes"]["TEXCOORD_0"])
                uvs.extend([[float(t[0]), float(t[1])] for t in uv_list])
                prim_uv_offset = len(uvs) - len(uv_list)
            if "indices" in prim:
                idx = [int(i[0]) for i in read_accessor(prim["indices"])]
            else:
                idx = list(range(len(pos)))
            for i in range(0, len(idx) - 2, 3):
                faces.append([base + idx[i], base + idx[i + 1], base + idx[i + 2]])
                if uv_list is not None:
                    face_uvs.append(
                        [prim_uv_offset + idx[i], prim_uv_offset + idx[i + 1], prim_uv_offset + idx[i + 2]]
                    )
                else:
                    face_uvs.append([])
    if not verts:
        raise core.invalid("glTF contains no triangle geometry.")
    mesh = Mesh(verts, faces, uvs, face_uvs, name=name)
    check_mesh_size(mesh)
    return mesh


def check_mesh_size(mesh):
    if len(mesh.verts) > MAX_VERTS:
        raise core.limit_exceeded(
            "Mesh has {0} vertices, above the {1} vertex ceiling.".format(len(mesh.verts), MAX_VERTS),
            "Decimate the mesh before using this Skill Tool, or raise MAX_VERTS in tools-o3d-geom.py.",
        )
    if mesh.tri_count > MAX_TRIS:
        raise core.limit_exceeded(
            "Mesh has {0} triangles, above the {1} triangle ceiling.".format(mesh.tri_count, MAX_TRIS),
            "Decimate or split the mesh, or raise MAX_TRIS in tools-o3d-geom.py.",
        )


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------


def write_obj(mesh, path, mtl_name=None):
    lines = ["# offline-3d-production OBJ export", "# name: {0}".format(mesh.name)]
    if mtl_name:
        lines.append("mtllib {0}".format(mtl_name))
        lines.append("usemtl default")
    lines.extend("v {0:.6f} {1:.6f} {2:.6f}".format(v[0], v[1], v[2]) for v in mesh.verts)
    for t in mesh.uvs:
        lines.append("vt {0:.6f} {1:.6f}".format(t[0], t[1]))
    for i, f in enumerate(mesh.faces):
        fuv = mesh.face_uvs[i] if i < len(mesh.face_uvs) else []
        if fuv and len(fuv) == len(f):
            lines.append("f " + " ".join("{0}/{1}".format(a + 1, b + 1) for a, b in zip(f, fuv)))
        else:
            lines.append("f " + " ".join(str(a + 1) for a in f))
    core.write_text_atomic(path, "\n".join(lines) + "\n")
    return path


def write_stl_binary(mesh, path):
    tris = mesh.tri_faces()
    buf = bytearray()
    header = "offline-3d-production binary STL".encode("ascii")
    buf.extend(header + b" " * (80 - len(header)))
    buf.extend(struct.pack("<I", len(tris)))
    for tri in tris:
        a, b, c = mesh.verts[tri[0]], mesh.verts[tri[1]], mesh.verts[tri[2]]
        n = tri_normal(a, b, c)
        buf.extend(struct.pack("<3f", n[0], n[1], n[2]))
        for v in (a, b, c):
            buf.extend(struct.pack("<3f", v[0], v[1], v[2]))
        buf.extend(struct.pack("<H", 0))
    core.write_bytes_atomic(path, bytes(buf))
    return path


def write_glb(mesh, path, textures=None):
    """Write a single-primitive glTF 2.0 binary file (.glb)."""
    verts = mesh.verts
    tris = mesh.tri_faces()
    pos_bytes = b"".join(struct.pack("<3f", v[0], v[1], v[2]) for v in verts)
    nrm = vertex_normals(mesh)
    nrm_bytes = b"".join(struct.pack("<3f", n[0], n[1], n[2]) for n in nrm)
    idx_bytes = b"".join(struct.pack("<I", i) for tri in tris for i in tri)
    chunks = [(pos_bytes, 34962), (nrm_bytes, 34962), (idx_bytes, 34963)]
    bin_data = b""
    views = []
    for data, target in chunks:
        while len(bin_data) % 4:
            bin_data += b"\x00"
        views.append({"buffer": 0, "byteOffset": len(bin_data), "byteLength": len(data), "target": target})
        bin_data += data
    while len(bin_data) % 4:
        bin_data += b"\x00"
    lo, hi = mesh.bounds()
    gltf = {
        "asset": {"version": "2.0", "generator": "offline-3d-production {0}".format(core.VERSION)},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": mesh.name or "mesh"}],
        "meshes": [
            {
                "name": mesh.name or "mesh",
                "primitives": [
                    {
                        "attributes": {"POSITION": 0, "NORMAL": 1},
                        "indices": 2,
                        "material": 0,
                        "mode": 4,
                    }
                ],
            }
        ],
        "materials": [
            {
                "name": "mat-default",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.8, 0.8, 0.8, 1.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.5,
                },
            }
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(verts),
                "type": "VEC3",
                "min": lo,
                "max": hi,
            },
            {"bufferView": 1, "componentType": 5126, "count": len(verts), "type": "VEC3"},
            {"bufferView": 2, "componentType": 5125, "count": len(tris) * 3, "type": "SCALAR"},
        ],
        "bufferViews": views,
        "buffers": [{"byteLength": len(bin_data)}],
    }
    if textures:
        gltf["extras"] = {"o3d": {"textures": textures}}
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4:
        json_bytes += b" "
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_data)
    out = bytearray()
    out.extend(b"glTF")
    out.extend(struct.pack("<II", 2, total))
    out.extend(struct.pack("<II", len(json_bytes), 0x4E4F534A))
    out.extend(json_bytes)
    out.extend(struct.pack("<II", len(bin_data), 0x004E4942))
    out.extend(bin_data)
    core.write_bytes_atomic(path, bytes(out))
    return path


def write_mesh_auto(mesh, path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".obj":
        return write_obj(mesh, path)
    if ext == ".stl":
        return write_stl_binary(mesh, path)
    if ext == ".glb":
        return write_glb(mesh, path)
    if ext == ".json":
        payload = {
            "name": mesh.name,
            "verts": mesh.verts,
            "faces": mesh.faces,
            "uvs": mesh.uvs,
            "face_uvs": mesh.face_uvs,
        }
        core.dump_json(path, payload)
        return path
    if ext == ".ply":
        return write_ply_ascii(mesh, path)
    raise core.invalid(
        "Unsupported mesh output format '{0}'.".format(ext or "(none)"),
        "Use .obj, .ply, .stl, .glb or .json.",
    )


def write_ply_ascii(mesh, path):
    lines = ["ply", "format ascii 1.0", "comment offline-3d-production export", "element vertex {0}".format(len(mesh.verts))]
    lines.extend(["property float x", "property float y", "property float z", "element face {0}".format(len(mesh.faces))])
    lines.extend(["property list uchar int vertex_indices", "end_header"])
    lines.extend("{0:.6f} {1:.6f} {2:.6f}".format(v[0], v[1], v[2]) for v in mesh.verts)
    lines.extend("{0} {1}".format(len(f), " ".join(str(i) for i in f)) for f in mesh.faces)
    core.write_text_atomic(path, "\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# Normals / topology analysis
# ---------------------------------------------------------------------------


def vertex_normals(mesh, smooth_angle_deg=None):
    acc = [[0.0, 0.0, 0.0] for _ in mesh.verts]
    for tri in mesh.tri_faces():
        a, b, c = mesh.verts[tri[0]], mesh.verts[tri[1]], mesh.verts[tri[2]]
        n = cross(sub(b, a), sub(c, a))
        for i in tri:
            acc[i][0] += n[0]
            acc[i][1] += n[1]
            acc[i][2] += n[2]
    return [normalize(n) for n in acc]


def corner_normals(mesh, smooth_angle_deg=40.0):
    """Per-face-corner normals with an auto-smooth angle threshold.

    Adjacent face normals are averaged only while they stay within
    ``smooth_angle_deg`` of the corner's own face, which keeps hard-surface
    edges crisp and curved surfaces smooth (the same rule DCC auto-smooth uses).
    Returns a list aligned with ``mesh.tri_faces()`` of three normals each.
    """
    tris = mesh.tri_faces()
    face_normals = []
    for tri in tris:
        face_normals.append(tri_normal(mesh.verts[tri[0]], mesh.verts[tri[1]], mesh.verts[tri[2]]))
    vertex_faces = {}
    for fi, tri in enumerate(tris):
        for i in tri:
            vertex_faces.setdefault(i, []).append(fi)
    cos_limit = math.cos(math.radians(max(0.0, min(180.0, smooth_angle_deg))))
    out = []
    cache = {}
    for fi, tri in enumerate(tris):
        fn = face_normals[fi]
        corners = []
        for i in tri:
            key = (i, round(fn[0], 3), round(fn[1], 3), round(fn[2], 3))
            cached = cache.get(key)
            if cached is not None:
                corners.append(cached)
                continue
            acc = [0.0, 0.0, 0.0]
            for other in vertex_faces.get(i, ()):
                on = face_normals[other]
                if dot(on, fn) >= cos_limit - 1e-9:
                    acc[0] += on[0]
                    acc[1] += on[1]
                    acc[2] += on[2]
            nrm = normalize(acc, fn if any(fn) else (0.0, 0.0, 1.0))
            cache[key] = nrm
            corners.append(nrm)
        out.append(corners)
    return out


def edge_map(mesh):
    """Undirected edge -> list of (face index, oriented pair)."""
    edges = {}
    for fi, f in enumerate(mesh.faces):
        n = len(f)
        for i in range(n):
            a, b = f[i], f[(i + 1) % n]
            key = (a, b) if a < b else (b, a)
            edges.setdefault(key, []).append((fi, (a, b)))
    return edges


def boundary_loops(mesh):
    edges = edge_map(mesh)
    boundary = [k for k, uses in edges.items() if len(uses) == 1]
    adj = {}
    for a, b in boundary:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    seen_edges = set()
    loops = []
    for a, b in boundary:
        key = (a, b) if a < b else (b, a)
        if key in seen_edges:
            continue
        loop = [a]
        seen_edges.add(key)
        prev, cur = a, b
        guard = 0
        while guard < len(boundary) + 2:
            guard += 1
            loop.append(cur)
            nxt = [x for x in adj.get(cur, []) if x != prev]
            if not nxt:
                break
            nxt.sort()
            prev, cur = cur, nxt[0]
            k2 = (prev, cur) if prev < cur else (cur, prev)
            if k2 in seen_edges:
                break
            seen_edges.add(k2)
            if cur == a:
                break
        loops.append(loop)
    return loops


def connected_components(mesh):
    parent = list(range(len(mesh.verts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for f in mesh.faces:
        for i in range(1, len(f)):
            union(f[0], f[i])
    groups = {}
    used = set()
    for f in mesh.faces:
        used.update(f)
    for v in sorted(used):
        groups.setdefault(find(v), []).append(v)
    return [groups[k] for k in sorted(groups)]


def degenerate_faces(mesh, area_eps=1e-12):
    out = []
    for fi, tri in enumerate(mesh.tri_faces()):
        if tri_area(mesh.verts[tri[0]], mesh.verts[tri[1]], mesh.verts[tri[2]]) <= area_eps:
            out.append(fi)
    return out


def valence_stats(mesh):
    counts = [0] * len(mesh.verts)
    for f in mesh.faces:
        for i in f:
            counts[i] += 1
    interior = [c for c in counts if c > 0]
    ngon_verts = [i for i, c in enumerate(counts) if c == 0]
    hist = {}
    for c in interior:
        hist[str(c)] = hist.get(str(c), 0) + 1
    poles = sum(1 for c in counts if c and c != 4)
    return {
        "histogram": dict(sorted(hist.items(), key=lambda kv: int(kv[0]))),
        "loose_vertices": len(ngon_verts),
        "non_quad_valence_vertices": poles,
        "max_valence": max(interior) if interior else 0,
    }


def face_quality(mesh):
    """Aspect ratio and minimum-angle statistics for triangles."""
    worst = 0.0
    min_angle = 180.0
    long_thin = 0
    tris = mesh.tri_faces()
    for tri in tris:
        a, b, c = mesh.verts[tri[0]], mesh.verts[tri[1]], mesh.verts[tri[2]]
        sides = sorted([length(sub(b, a)), length(sub(c, b)), length(sub(a, c))])
        if sides[0] <= EPS:
            continue
        ratio = sides[2] / sides[0]
        worst = max(worst, ratio)
        if ratio > 8.0:
            long_thin += 1
        for i in range(3):
            p = [a, b, c][i]
            q = [a, b, c][(i + 1) % 3]
            r = [a, b, c][(i + 2) % 3]
            e1, e2 = normalize(sub(q, p)), normalize(sub(r, p))
            cang = max(-1.0, min(1.0, dot(e1, e2)))
            min_angle = min(min_angle, math.degrees(math.acos(cang)))
    return {
        "triangles": len(tris),
        "worst_aspect_ratio": round(worst, 4),
        "min_angle_degrees": round(min_angle, 4) if tris else None,
        "long_thin_triangles": long_thin,
    }


def winding_consistency(mesh):
    """Count edges traversed in the same direction twice (bad) vs opposite (good)."""
    seen = {}
    inconsistent = 0
    for f in mesh.faces:
        n = len(f)
        for i in range(n):
            a, b = f[i], f[(i + 1) % n]
            key = (a, b)
            rev = (b, a)
            if key in seen:
                inconsistent += 1
            seen[key] = seen.get(key, 0) + 1
    nonmanifold = sum(1 for k, v in seen.items() if v > 1)
    return {"same_direction_edge_reuses": inconsistent, "nonmanifold_directed_edges": nonmanifold}


def symmetry_report(mesh, axis="X", tolerance=0.0015):
    """Match mirrored vertices with a spatial hash; report asymmetric share."""
    if axis not in ("X", "Y", "Z"):
        raise core.invalid("Symmetry axis must be X, Y or Z.")
    idx = {"X": 0, "Y": 1, "Z": 2}[axis]
    lo, hi = mesh.bounds()
    mid = (lo[idx] + hi[idx]) / 2.0
    cell = max(tolerance * 4.0, 1e-6)
    grid = {}
    for i, v in enumerate(mesh.verts):
        key = (
            int(math.floor((v[0] - lo[0]) / cell)),
            int(math.floor((v[1] - lo[1]) / cell)),
            int(math.floor((v[2] - lo[2]) / cell)),
        )
        grid.setdefault(key, []).append(i)
    matched = 0
    worst = 0.0
    unmatched = []
    for i, v in enumerate(mesh.verts):
        target = list(v)
        target[idx] = 2.0 * mid - v[idx]
        found = None
        base = (
            int(math.floor((target[0] - lo[0]) / cell)),
            int(math.floor((target[1] - lo[1]) / cell)),
            int(math.floor((target[2] - lo[2]) / cell)),
        )
        best = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in grid.get((base[0] + dx, base[1] + dy, base[2] + dz), ()):
                        d = length(sub(mesh.verts[j], target))
                        if best is None or d < best:
                            best, found = d, j
        if found is not None and best <= tolerance:
            matched += 1
            worst = max(worst, best)
        else:
            unmatched.append(i)
    return {
        "axis": axis,
        "tolerance": tolerance,
        "vertices": len(mesh.verts),
        "matched": matched,
        "matched_ratio": round(matched / len(mesh.verts), 6) if mesh.verts else 0.0,
        "unmatched_vertices": len(unmatched),
        "unmatched_sample": unmatched[:20],
        "worst_match_distance": round(worst, 9),
    }


def self_intersections(mesh, max_pairs=400000, limit_hits=200):
    """Rough self-intersection screen using a uniform grid broadphase."""
    tris = mesh.tri_faces()
    if not tris:
        return {"tested_pairs": 0, "hits": 0, "truncated": False, "triangles": 0}
    lo, hi = mesh.bounds()
    size = max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) or 1.0
    res = max(1, min(48, int(round(len(tris) ** (1.0 / 3.0) * 2))))
    cell = size / res or 1.0
    boxes = []
    grid = {}
    for ti, tri in enumerate(tris):
        xs = [mesh.verts[i] for i in tri]
        tlo = [min(p[k] for p in xs) for k in range(3)]
        thi = [max(p[k] for p in xs) for k in range(3)]
        boxes.append((tlo, thi))
        k0 = [int((tlo[k] - lo[k]) / cell) for k in range(3)]
        k1 = [int((thi[k] - lo[k]) / cell) for k in range(3)]
        for x in range(k0[0], k1[0] + 1):
            for y in range(k0[1], k1[1] + 1):
                for z in range(k0[2], k1[2] + 1):
                    grid.setdefault((x, y, z), []).append(ti)
    tested = 0
    hits = []
    truncated = False
    seen = set()
    for key in sorted(grid):
        bucket = grid[key]
        if len(bucket) < 2:
            continue
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                a, b = bucket[i], bucket[j]
                pair = (a, b) if a < b else (b, a)
                if pair in seen:
                    continue
                seen.add(pair)
                if tested >= max_pairs:
                    truncated = True
                    break
                tested += 1
                ta, tb = tris[pair[0]], tris[pair[1]]
                if set(ta) & set(tb):
                    continue
                if not aabb_overlap(boxes[pair[0]], boxes[pair[1]]):
                    continue
                if triangles_intersect(
                    mesh.verts[ta[0]], mesh.verts[ta[1]], mesh.verts[ta[2]],
                    mesh.verts[tb[0]], mesh.verts[tb[1]], mesh.verts[tb[2]],
                ):
                    hits.append({"a": pair[0], "b": pair[1]})
                    if len(hits) >= limit_hits:
                        truncated = True
                        break
            if truncated and len(hits) >= limit_hits:
                break
        if truncated and len(hits) >= limit_hits:
            break
    return {
        "triangles": len(tris),
        "tested_pairs": tested,
        "hits": len(hits),
        "sample": hits[:20],
        "truncated": truncated,
    }


def aabb_overlap(a, b, eps=1e-9):
    for k in range(3):
        if a[1][k] < b[0][k] - eps or b[1][k] < a[0][k] - eps:
            return False
    return True


def triangles_intersect(p1, q1, r1, p2, q2, r2):
    n2 = tri_normal(p2, q2, r2)
    d1 = [dot(n2, sub(v, p2)) for v in (p1, q1, r1)]
    if (d1[0] > EPS and d1[1] > EPS and d1[2] > EPS) or (d1[0] < -EPS and d1[1] < -EPS and d1[2] < -EPS):
        return False
    n1 = tri_normal(p1, q1, r1)
    d2 = [dot(n1, sub(v, p1)) for v in (p2, q2, r2)]
    if (d2[0] > EPS and d2[1] > EPS and d2[2] > EPS) or (d2[0] < -EPS and d2[1] < -EPS and d2[2] < -EPS):
        return False
    for tri_a, tri_b in (((p1, q1, r1), (p2, q2, r2)), ((p2, q2, r2), (p1, q1, r1))):
        for i in range(3):
            a = tri_a[i]
            b = tri_a[(i + 1) % 3]
            if segment_triangle(a, b, tri_b[0], tri_b[1], tri_b[2]):
                return True
    return False


def segment_triangle(p, q, a, b, c):
    d = sub(q, p)
    e1 = sub(b, a)
    e2 = sub(c, a)
    h = cross(d, e2)
    det = dot(e1, h)
    if abs(det) < 1e-12:
        return False
    f = 1.0 / det
    s = sub(p, a)
    u = f * dot(s, h)
    if u < -1e-9 or u > 1 + 1e-9:
        return False
    qv = cross(s, e1)
    v = f * dot(d, qv)
    if v < -1e-9 or u + v > 1 + 1e-9:
        return False
    t = f * dot(e2, qv)
    return 1e-9 < t < 1 - 1e-9


# ---------------------------------------------------------------------------
# Repair and conversion
# ---------------------------------------------------------------------------


def weld_vertices(mesh, distance=1e-5):
    """Merge vertices closer than ``distance``; deterministic grid hashing."""
    cell = max(distance, 1e-9)
    remap = [None] * len(mesh.verts)
    kept = []
    grid = {}
    for i, v in enumerate(mesh.verts):
        key = (
            int(math.floor(v[0] / cell)),
            int(math.floor(v[1] / cell)),
            int(math.floor(v[2] / cell)),
        )
        found = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in grid.get((key[0] + dx, key[1] + dy, key[2] + dz), ()):
                        if length(sub(kept[j], v)) <= distance:
                            found = j
                            break
                    if found is not None:
                        break
                if found is not None:
                    break
            if found is not None:
                break
        if found is None:
            kept.append(list(v))
            j = len(kept) - 1
            grid.setdefault(key, []).append(j)
        else:
            j = found
        remap[i] = j
    faces = []
    for f in mesh.faces:
        nf = []
        for i in f:
            r = remap[i]
            if not nf or nf[-1] != r:
                nf.append(r)
        if len(nf) > 2 and nf[0] == nf[-1]:
            nf.pop()
        if len(nf) >= 3:
            faces.append(nf)
    out = Mesh(kept, faces, [list(t) for t in mesh.uvs], [], mesh.name, dict(mesh.source))
    return out, {"removed_vertices": len(mesh.verts) - len(kept), "removed_faces": len(mesh.faces) - len(faces)}


def remove_degenerate_faces(mesh, area_eps=1e-14):
    keep = [i for i, tri in enumerate(mesh.tri_faces()) if tri_area(mesh.verts[tri[0]], mesh.verts[tri[1]], mesh.verts[tri[2]]) > area_eps]
    tris = mesh.tri_faces()
    faces = [tris[i] for i in keep]
    uvs, face_uvs = mesh.uvs, []
    out = Mesh([list(v) for v in mesh.verts], [list(f) for f in faces], [list(t) for t in uvs], face_uvs, mesh.name, dict(mesh.source))
    return out, {"removed_faces": len(tris) - len(faces)}


def remove_loose_vertices(mesh):
    used = set()
    for f in mesh.faces:
        used.update(f)
    remap = {}
    verts = []
    for i, v in enumerate(mesh.verts):
        if i in used:
            remap[i] = len(verts)
            verts.append(list(v))
    faces = [[remap[i] for i in f] for f in mesh.faces]
    out = Mesh(verts, faces, [list(t) for t in mesh.uvs], list(mesh.face_uvs), mesh.name, dict(mesh.source))
    return out, {"removed_vertices": len(mesh.verts) - len(verts)}


def unify_winding(mesh):
    """Propagate consistent winding across each connected component."""
    tris = mesh.tri_faces()
    edges = {}
    for fi, tri in enumerate(tris):
        for i in range(3):
            a, b = tri[i], tri[(i + 1) % 3]
            edges.setdefault((a, b) if a < b else (b, a), []).append(fi)
    flipped = [False] * len(tris)
    visited = [False] * len(tris)
    adj = {}
    for use in edges.values():
        if len(use) == 2:
            adj.setdefault(use[0], []).append(use[1])
            adj.setdefault(use[1], []).append(use[0])
    flips = 0
    for start in range(len(tris)):
        if visited[start]:
            continue
        visited[start] = True
        stack = [start]
        while stack:
            cur = stack.pop()
            for nb in adj.get(cur, ()):
                if visited[nb]:
                    continue
                visited[nb] = True
                # consistent neighbours must traverse the shared edge oppositely
                shared = None
                for i in range(3):
                    a, b = tris[cur][i], tris[cur][(i + 1) % 3]
                    for j in range(3):
                        c, d = tris[nb][j], tris[nb][(j + 1) % 3]
                        if a == d and b == c:
                            shared = "opposite"
                        elif a == c and b == d:
                            shared = "same"
                if shared == "same":
                    tris[nb] = [tris[nb][0], tris[nb][2], tris[nb][1]]
                    flipped[nb] = True
                    flips += 1
                stack.append(nb)
    mesh.faces = [list(t) for t in tris]
    mesh.invalidate()
    return mesh, {"flipped_faces": flips}


def fill_holes(mesh, max_edges=64):
    """Fan-fill small boundary loops (bounded by ``max_edges``)."""
    loops = boundary_loops(mesh)
    added = 0
    handled = 0
    for loop in loops:
        if len(loop) < 3 or len(loop) > max_edges:
            continue
        center = [0.0, 0.0, 0.0]
        for i in loop:
            center = add(center, mesh.verts[i])
        center = [c / len(loop) for c in center]
        ci = len(mesh.verts)
        mesh.verts.append(center)
        for i in range(len(loop)):
            a, b = loop[i], loop[(i + 1) % len(loop)]
            mesh.faces.append([b, a, ci])
            added += 1
        handled += 1
    mesh.invalidate()
    return mesh, {"filled_loops": handled, "added_faces": added, "remaining_loops": len(loops) - handled}


def repair_mesh(mesh, weld_distance=1e-5, do_fill=True, max_hole_edges=64):
    report = {}
    mesh, r1 = weld_vertices(mesh, weld_distance)
    report["weld"] = r1
    mesh, r2 = remove_degenerate_faces(mesh)
    report["degenerate"] = r2
    mesh, r3 = unify_winding(mesh)
    report["winding"] = r3
    if do_fill:
        mesh, r4 = fill_holes(mesh, max_hole_edges)
        report["holes"] = r4
    mesh, r5 = remove_loose_vertices(mesh)
    report["loose"] = r5
    return mesh, report


# ---------------------------------------------------------------------------
# Decimation, remesh, subdivision, smoothing
# ---------------------------------------------------------------------------


def cluster_decimate(mesh, target_tris, grid_hint=None):
    """Deterministic vertex-clustering decimation to a triangle budget.

    Chosen over quadric edge collapse because it is fast, stable in pure
    Python, and produces predictable vertex correspondence for gameplay LODs.
    """
    tris = mesh.tri_faces()
    start_tris = len(tris)
    if target_tris >= start_tris:
        return mesh, {"start_triangles": start_tris, "end_triangles": start_tris, "ratio": 1.0}
    lo, hi = mesh.bounds()
    diag = length(sub(hi, lo)) or 1.0
    best = None
    # Search cell sizes (deterministic increasing list) until the budget fits.
    for step in range(0, 60):
        factor = 0.004 * (1.12 ** step) * diag
        verts, faces, index = [], [], {}
        for v in mesh.verts:
            key = (
                int(math.floor((v[0] - lo[0]) / factor)),
                int(math.floor((v[1] - lo[1]) / factor)),
                int(math.floor((v[2] - lo[2]) / factor)),
            )
            if key not in index:
                index[key] = []
            index[key].append(v)
        keys = sorted(index)
        remap = {}
        for key in keys:
            pts = index[key]
            c = [sum(p[k] for p in pts) / len(pts) for k in range(3)]
            remap[key] = len(verts)
            verts.append(c)
        new_faces = []
        for tri in tris:
            ids = []
            for i in tri:
                v = mesh.verts[i]
                key = (
                    int(math.floor((v[0] - lo[0]) / factor)),
                    int(math.floor((v[1] - lo[1]) / factor)),
                    int(math.floor((v[2] - lo[2]) / factor)),
                )
                ids.append(remap[key])
            dedup = []
            for i in ids:
                if i not in dedup:
                    dedup.append(i)
            if len(dedup) >= 3 and tri_area(verts[dedup[0]], verts[dedup[1]], verts[dedup[2]]) > 1e-14:
                new_faces.append(dedup)
        candidate = (len(new_faces), verts, new_faces)
        best = candidate
        if len(new_faces) <= target_tris:
            break
    verts, faces = best[1], best[2]
    out = Mesh(verts, faces, name=mesh.name, source=dict(mesh.source))
    out, _ = weld_vertices(out, 1e-7)
    out, _ = remove_degenerate_faces(out)
    return out, {
        "start_triangles": start_tris,
        "end_triangles": out.tri_count,
        "ratio": round(out.tri_count / start_tris, 6) if start_tris else 0.0,
        "method": "vertex-clustering",
    }


def voxel_remesh(mesh, voxel_size, max_tris=200000, smooth_iterations=2):
    """Voxel (blocky-surface) remesh, then smooth into a watertight base mesh.

    Method: rasterise the source triangles onto a voxel grid, solidify the
    interior along Z, then emit one quad per solid/empty voxel face, identifying
    corner vertices so the result is closed and manifold. Two Laplacian passes
    remove the stair-stepping. This is a deterministic, dependency-free
    alternative to a DCC remesher; it is intentionally *not* an adaptive
    surface-nets implementation, so detail smaller than the voxel size is lost.
    """
    lo, hi = mesh.bounds()
    if voxel_size <= 0:
        raise core.invalid("voxel_size must be positive.", "Use roughly 1/60 of the model height.")
    dims = [max(2, int(math.ceil((hi[k] - lo[k]) / voxel_size)) + 3) for k in range(3)]
    total = dims[0] * dims[1] * dims[2]
    if total > 12_000_000:
        raise core.limit_exceeded(
            "Remesh grid would be {0} voxels ({1}x{2}x{3}).".format(total, *dims),
            "Increase --voxel-size or bound the mesh first.",
            {"grid": dims},
        )
    origin = [lo[k] - voxel_size * 1.5 for k in range(3)]
    occ = bytearray(total)

    def idx(x, y, z):
        return (z * dims[1] + y) * dims[0] + x

    tris = mesh.tri_faces()
    if not tris:
        raise core.invalid("Mesh has no triangles to remesh.")
    for tri in tris:
        a, b, c = mesh.verts[tri[0]], mesh.verts[tri[1]], mesh.verts[tri[2]]
        edge = max(length(sub(b, a)), length(sub(c, b)), length(sub(a, c)))
        steps = max(1, int(math.ceil(edge / (voxel_size * 0.4))))
        for i in range(steps + 1):
            for j in range(steps + 1 - i):
                u = i / float(steps)
                v = j / float(steps)
                point = [
                    a[0] + (b[0] - a[0]) * u + (c[0] - a[0]) * v,
                    a[1] + (b[1] - a[1]) * u + (c[1] - a[1]) * v,
                    a[2] + (b[2] - a[2]) * u + (c[2] - a[2]) * v,
                ]
                gx = int((point[0] - origin[0]) / voxel_size)
                gy = int((point[1] - origin[1]) / voxel_size)
                gz = int((point[2] - origin[2]) / voxel_size)
                if 0 <= gx < dims[0] and 0 <= gy < dims[1] and 0 <= gz < dims[2]:
                    occ[idx(gx, gy, gz)] = 1
    # Solidify: fill between the first and last surface crossing on each Z ray.
    for y in range(dims[1]):
        for x in range(dims[0]):
            hits = [z for z in range(dims[2]) if occ[idx(x, y, z)]]
            for k in range(len(hits) - 1):
                for z in range(hits[k], hits[k + 1]):
                    occ[idx(x, y, z)] = 1
    solid = sum(occ)
    if not solid:
        raise core.O3DError(
            "rasterisation_empty",
            "No voxel was touched; the mesh may be empty or far smaller than the voxel size.",
            "Lower --voxel-size (try 1/60 of the model height) and re-run.",
        )
    # Emit one quad per solid/empty face; share corner vertices.
    corner_index = {}
    verts = []

    def corner(ix, iy, iz):
        key = (ix, iy, iz)
        existing = corner_index.get(key)
        if existing is not None:
            return existing
        corner_index[key] = len(verts)
        verts.append([origin[0] + ix * voxel_size, origin[1] + iy * voxel_size, origin[2] + iz * voxel_size])
        return corner_index[key]

    faces = []
    dirs = [
        ((1, 0, 0), [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)]),
        ((-1, 0, 0), [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)]),
        ((0, 1, 0), [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)]),
        ((0, -1, 0), [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]),
        ((0, 0, 1), [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]),
        ((0, 0, -1), [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)]),
    ]
    for z in range(dims[2]):
        for y in range(dims[1]):
            for x in range(dims[0]):
                if not occ[idx(x, y, z)]:
                    continue
                for (dx, dy, dz), quad in dirs:
                    nx, ny, nz = x + dx, y + dy, z + dz
                    inside = (0 <= nx < dims[0] and 0 <= ny < dims[1] and 0 <= nz < dims[2]
                              and occ[idx(nx, ny, nz)])
                    if inside:
                        continue
                    ids = [corner(x + cx, y + cy, z + cz) for cx, cy, cz in quad]
                    faces.append([ids[0], ids[1], ids[3]])
                    faces.append([ids[0], ids[3], ids[2]])
                    if len(faces) > max_tris * 2:
                        raise core.limit_exceeded(
                            "Voxel remesh exceeded the {0} triangle ceiling.".format(max_tris),
                            "Increase --voxel-size.",
                        )
    if not faces:
        raise core.O3DError(
            "remesh_empty_surface",
            "Voxel remesh produced no faces; the source has no closed surface at this voxel size.",
            "Lower --voxel-size, or fill the holes first with: mesh repair --fill",
        )
    out = Mesh(verts, faces, name=mesh.name, source=dict(mesh.source))
    out, _ = weld_vertices(out, voxel_size * 1e-6)
    out, _ = unify_winding(out)
    raw_tris = out.tri_count
    if smooth_iterations:
        out = smooth(out, smooth_iterations, 0.5, preserve_boundary=False)
        out, _ = unify_winding(out)
    if out.signed_volume() < 0:
        for f in out.faces:
            f.reverse()
        out.invalidate()
    out, report = repair_mesh(out, weld_distance=voxel_size * 1e-4, do_fill=True)
    report.update({
        "method": "voxel-block-surface+laplacian",
        "voxel_size": voxel_size,
        "grid": dims,
        "solid_voxels": solid,
        "blocky_triangles": raw_tris,
        "smooth_iterations": smooth_iterations,
        "end_triangles": out.tri_count,
        "end_volume": round(out.signed_volume(), 6),
    })
    return out, report


def subdivide_catmull_clark(mesh, levels=1, max_tris=400000):
    """Catmull-Clark subdivision; works on mixed tri/quad cages."""
    current = mesh
    for _ in range(max(0, int(levels))):
        current = _cc_once(current)
        if current.tri_count > max_tris:
            raise core.limit_exceeded(
                "Subdivision exceeded the {0} triangle ceiling.".format(max_tris),
                "Reduce --levels.",
            )
    return current


def _cc_once(mesh):
    faces = mesh.faces
    if not faces:
        return mesh.clone()
    face_points = []
    for f in faces:
        pts = [mesh.verts[i] for i in f]
        face_points.append([sum(p[k] for p in pts) / len(pts) for k in range(3)])
    edge_faces = {}
    for fi, f in enumerate(faces):
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            key = (a, b) if a < b else (b, a)
            edge_faces.setdefault(key, []).append(fi)
    edge_point_index = {}
    new_verts = [list(v) for v in mesh.verts]
    for key in sorted(edge_faces):
        a, b = key
        mid = [(mesh.verts[a][k] + mesh.verts[b][k]) / 2.0 for k in range(3)]
        linked = edge_faces[key]
        if len(linked) == 2:
            fp = [(face_points[linked[0]][k] + face_points[linked[1]][k]) / 2.0 for k in range(3)]
            p = [(mid[k] + fp[k]) / 2.0 for k in range(3)]
        else:
            p = mid
        edge_point_index[key] = len(new_verts)
        new_verts.append(p)
    vert_faces = {}
    vert_edges = {}
    for fi, f in enumerate(faces):
        for i in range(len(f)):
            v = f[i]
            vert_faces.setdefault(v, []).append(fi)
            a, b = f[i], f[(i + 1) % len(f)]
            key = (a, b) if a < b else (b, a)
            vert_edges.setdefault(v, set()).add(key)
    for v in sorted(vert_edges):
        if len(vert_edges[v]) < 2:
            continue
        n = len(vert_faces[v])
        boundary = [k for k in vert_edges[v] if len(edge_faces[k]) == 1]
        if boundary:
            acc = [0.0, 0.0, 0.0]
            for k in boundary:
                other = k[1] if k[0] == v else k[0]
                acc = add(acc, mesh.verts[other])
            new_verts[v] = [
                (mesh.verts[v][i] * 6.0 + acc[i]) / 8.0 for i in range(3)
            ]
        else:
            fp = [0.0, 0.0, 0.0]
            for fi in vert_faces[v]:
                fp = add(fp, face_points[fi])
            fp = [c / n for c in fp]
            rp = [0.0, 0.0, 0.0]
            keys = sorted(vert_edges[v])
            for k in keys:
                other = k[1] if k[0] == v else k[0]
                rp = add(rp, [(mesh.verts[v][i] + mesh.verts[other][i]) / 2.0 for i in range(3)])
            rp = [c / len(keys) for c in rp]
            new_verts[v] = [(fp[i] + 2.0 * rp[i] + (n - 3.0) * mesh.verts[v][i]) / n for i in range(3)]
    new_faces = []
    for fi, f in enumerate(faces):
        n = len(f)
        for i in range(n):
            v = f[i]
            prev = f[(i - 1) % n]
            nxt = f[(i + 1) % n]
            e_prev = edge_point_index[(v, prev) if v < prev else (prev, v)]
            e_next = edge_point_index[(v, nxt) if v < nxt else (nxt, v)]
            new_faces.append([v, e_next, len(new_verts), e_prev])
        new_verts.append(list(face_points[fi]))
    return Mesh(new_verts, new_faces, name=mesh.name, source=dict(mesh.source))


def smooth(mesh, iterations=2, factor=0.5, preserve_boundary=True):
    """Umbrella/Laplacian smoothing with optional boundary pinning."""
    if not (0 < factor <= 1):
        raise core.invalid("smoothing factor must be in (0, 1].")
    boundary = set()
    for loop in boundary_loops(mesh):
        boundary.update(loop)
    verts = [list(v) for v in mesh.verts]
    neigh = {}
    for f in mesh.faces:
        n = len(f)
        for i in range(n):
            a, b = f[i], f[(i + 1) % n]
            neigh.setdefault(a, set()).add(b)
            neigh.setdefault(b, set()).add(a)
    for _ in range(max(0, int(iterations))):
        new = [list(v) for v in verts]
        for i, v in enumerate(verts):
            if preserve_boundary and i in boundary:
                continue
            ns = neigh.get(i)
            if not ns:
                continue
            avg = [0.0, 0.0, 0.0]
            for j in sorted(ns):
                avg = add(avg, verts[j])
            avg = [c / len(ns) for c in avg]
            new[i] = [v[k] + factor * (avg[k] - v[k]) for k in range(3)]
        verts = new
    out = Mesh(verts, [list(f) for f in mesh.faces], name=mesh.name, source=dict(mesh.source))
    return out


def sample_surface(mesh, count=1000, seed=0):
    tris = mesh.tri_faces()
    if not tris:
        raise core.invalid("Cannot sample an empty mesh.")
    areas = []
    total = 0.0
    for tri in tris:
        a = tri_area(mesh.verts[tri[0]], mesh.verts[tri[1]], mesh.verts[tri[2]])
        areas.append(a)
        total += a
    if total <= 0:
        raise core.invalid("Mesh has no surface area to sample.")
    rng = random.Random(seed)
    cum = []
    run = 0.0
    for a in areas:
        run += a
        cum.append(run)
    import bisect

    pts = []
    for _ in range(count):
        r = rng.random() * total
        ti = min(len(cum) - 1, bisect.bisect_left(cum, r))
        u, v = rng.random(), rng.random()
        if u + v > 1:
            u, v = 1 - u, 1 - v
        a, b, c = (mesh.verts[i] for i in tris[ti])
        pts.append(
            [
                a[0] + (b[0] - a[0]) * u + (c[0] - a[0]) * v,
                a[1] + (b[1] - a[1]) * u + (c[1] - a[1]) * v,
                a[2] + (b[2] - a[2]) * u + (c[2] - a[2]) * v,
            ]
        )
    return pts


# ---------------------------------------------------------------------------
# UV analysis
# ---------------------------------------------------------------------------


def uv_report(mesh, texel_resolution=2048, max_faces=200000):
    if not mesh.uvs or not mesh.face_uvs:
        raise core.invalid(
            "Mesh has no UV coordinates (need 'vt' records or TEXCOORD_0).",
            "Supply a UV-mapped mesh, or run the uv command on a mesh that has UVs.",
        )
    stretch = []
    positive = 0
    negative = 0
    zero_area = 0
    overlap_probe = {}
    uv_faces = 0
    for fi, tri in enumerate(mesh.tri_faces()):
        fuv = mesh.face_uvs[fi] if fi < len(mesh.face_uvs) else []
        if len(fuv) != 3:
            continue
        uv_faces += 1
        p = [mesh.uvs[i] for i in fuv]
        su = [p[1][0] - p[0][0], p[1][1] - p[0][1]]
        sv = [p[2][0] - p[0][0], p[2][1] - p[0][1]]
        det = su[0] * sv[1] - su[1] * sv[0]
        uv_area = abs(det) / 2.0
        if det > 0:
            positive += 1
        elif det < 0:
            negative += 1
        if uv_area < 1e-12:
            zero_area += 1
            continue
        a, b, c = mesh.verts[tri[0]], mesh.verts[tri[1]], mesh.verts[tri[2]]
        n = tri_normal(a, b, c)
        t = normalize(sub(b, a))
        bt = cross(n, t)
        area3 = tri_area(a, b, c)
        if area3 <= 0:
            continue
        ratio = math.sqrt(uv_area / area3)
        stretch.append(ratio)
        key = (round(p[0][0] * 128), round(p[0][1] * 128))
        overlap_probe[key] = overlap_probe.get(key, 0) + 1
        if uv_faces >= max_faces:
            break
    if not stretch:
        raise core.invalid("No non-degenerate UV triangles found; cannot analyse distortion.")
    stretch.sort()
    n = len(stretch)
    med = stretch[n // 2]
    p05 = stretch[int(n * 0.05)]
    p95 = stretch[min(n - 1, int(n * 0.95))]
    bbox_area = max(1e-9, (max(p[0] for p in mesh.uvs) - min(p[0] for p in mesh.uvs)) *
                      (max(p[1] for p in mesh.uvs) - min(p[1] for p in mesh.uvs)))
    # Absolute UV winding is a convention (image V axis), so the defect is
    # *inconsistency*: a mesh should not mix orientations.
    flipped = min(positive, negative) if (positive and negative) else 0
    return {
        "uv_triangles": uv_faces,
        "uv_orientation_positive": positive,
        "uv_orientation_negative": negative,
        "dominant_uv_orientation": "positive" if positive >= negative else "negative",
        "flipped_uv_triangles": flipped,
        "zero_area_uv_triangles": zero_area,
        "stretch_median": round(med, 6),
        "stretch_p05": round(p05, 6),
        "stretch_p95": round(p95, 6),
        "stretch_ratio_p95_over_p05": round(p95 / p05, 4) if p05 > 0 else None,
        "texel_density_px_per_m": round(med * math.sqrt(texel_resolution * texel_resolution), 3),
        "uv_bounds": [
            round(min(p[0] for p in mesh.uvs), 6),
            round(min(p[1] for p in mesh.uvs), 6),
            round(max(p[0] for p in mesh.uvs), 6),
            round(max(p[1] for p in mesh.uvs), 6),
        ],
        "uv_bounds_area": round(bbox_area, 6),
        "suspected_overlap_cells": sum(1 for v in overlap_probe.values() if v > 3),
    }


def apply_axis_conversion(mesh, up_from, up_to, forward_from=None):
    """Rotate the mesh between two up-axis conventions (X/Y/Z)."""
    def rot_matrix(frm, to):
        # rotate so that 'frm' axis maps onto 'to' axis
        if frm == to:
            return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        axis = {"X": 0, "Y": 1, "Z": 2}
        a, b = axis[frm], axis[to]
        if (a + 1) % 3 == b:
            # +90 deg about the remaining axis
            other = 3 - a - b
            c, s = 0.0, 1.0
        else:
            other = 3 - a - b
            c, s = 0.0, -1.0
        m = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
        i, j = (a + 1) % 3, (a + 2) % 3
        # generic rotation about 'other'
        m = [[1.0 if x == y else 0.0 for y in range(4)] for x in range(4)]
        o = other
        p, q = (o + 1) % 3, (o + 2) % 3
        m[p][p] = c
        m[p][q] = -s
        m[q][p] = s
        m[q][q] = c
        # ensure the mapping lands where requested; fall back to 180 flips
        test = [0.0, 0.0, 0.0]
        test[a] = 1.0
        res = [
            m[0][0] * test[0] + m[0][1] * test[1] + m[0][2] * test[2],
            m[1][0] * test[0] + m[1][1] * test[1] + m[1][2] * test[2],
            m[2][0] * test[0] + m[2][1] * test[1] + m[2][2] * test[2],
        ]
        if max(range(3), key=lambda k: abs(res[k])) != b or res[b] < 0:
            # 180 degrees about a third axis achieves the remaining mapping
            m = [[1.0 if x == y else 0.0 for y in range(4)] for x in range(4)]
            r = p
            m[r][r] = -1.0
            if r == b:
                m = [[1.0 if x == y else 0.0 for y in range(4)] for x in range(4)]
                m[a][a] = -1.0
                m[b][b] = -1.0
        return m

    m = rot_matrix(up_from.upper(), up_to.upper())
    mesh.transform(m)
    return mesh, {"up_from": up_from.upper(), "up_to": up_to.upper(), "matrix": m}


UNIT_SCALES = {
    "mm": 0.001,
    "cm": 0.01,
    "m": 1.0,
    "in": 0.0254,
    "ft": 0.3048,
    "yd": 0.9144,
    "km": 1000.0,
    "mil": 0.0000254,
}


def unit_factor(from_unit, to_unit):
    if from_unit not in UNIT_SCALES:
        raise core.invalid("Unknown source unit '{0}'.".format(from_unit), "Use one of: " + ", ".join(sorted(UNIT_SCALES)))
    if to_unit not in UNIT_SCALES:
        raise core.invalid("Unknown target unit '{0}'.".format(to_unit), "Use one of: " + ", ".join(sorted(UNIT_SCALES)))
    return UNIT_SCALES[from_unit] / UNIT_SCALES[to_unit]


def make_grid(divisions, size=1.0, centered=True):
    """Closed cube grid used by fixtures and smoke tests."""
    n = max(1, int(divisions))
    verts, faces = [], []
    step = size / n
    start = -size / 2.0 if centered else 0.0
    for axis in range(3):
        for sign in (-1, 1):
            plane = start + (size if sign > 0 else 0.0)
            base = len(verts)
            for i in range(n + 1):
                for j in range(n + 1):
                    v = [0.0, 0.0, 0.0]
                    a1, a2 = (axis + 1) % 3, (axis + 2) % 3
                    v[axis] = plane
                    v[a1] = start + i * step
                    v[a2] = start + j * step
                    verts.append(v)
            for i in range(n):
                for j in range(n):
                    a = base + i * (n + 1) + j
                    b = a + 1
                    c = a + (n + 1) + 1
                    d = a + (n + 1)
                    if sign > 0:
                        faces.append([a, b, c])
                        faces.append([a, c, d])
                    else:
                        faces.append([a, c, b])
                        faces.append([a, d, c])
    mesh = Mesh(verts, faces, name="grid-cube")
    mesh, _ = weld_vertices(mesh, 1e-7)
    mesh, _ = unify_winding(mesh)
    if mesh.signed_volume() < 0:
        for f in mesh.faces:
            f.reverse()
        mesh.invalidate()
    return mesh


def make_uv_sphere(segments=24, rings=16, radius=0.5):
    """UV sphere with seam-aware spherical UVs (rebuilt after welding).

    UVs are assigned per face corner from the vertex direction, so welding the
    pole/seam vertices cannot desynchronise the UV indices.
    """
    verts, faces = [], []
    for r in range(rings + 1):
        phi = math.pi * r / rings
        for s in range(segments + 1):
            theta = 2.0 * math.pi * s / segments
            verts.append(
                [
                    radius * math.sin(phi) * math.cos(theta),
                    radius * math.cos(phi),
                    radius * math.sin(phi) * math.sin(theta),
                ]
            )
    for r in range(rings):
        for s in range(segments):
            a = r * (segments + 1) + s
            b = a + segments + 1
            faces.append([a, a + 1, b])
            faces.append([a + 1, b + 1, b])
    mesh = Mesh(verts, faces, name="uv-sphere")
    mesh, _ = weld_vertices(mesh, 1e-7)
    if mesh.signed_volume() < 0:
        for f in mesh.faces:
            f.reverse()
        mesh.invalidate()
    assign_spherical_uvs(mesh)
    return mesh


def assign_spherical_uvs(mesh, seam_channel=0):
    """Assign a seam-aware spherical UV layout to a mesh in place."""
    uvs = []
    face_uvs = []
    two_pi = 2.0 * math.pi

    def uv_of(v):
        x, y, z = v
        r = math.sqrt(x * x + y * y + z * z) or 1.0
        u = (math.atan2(z, x) / two_pi) + 0.5
        vv = 1.0 - (math.acos(max(-1.0, min(1.0, y / r))) / math.pi)
        return u, vv

    for tri in mesh.tri_faces():
        corners = [uv_of(mesh.verts[i]) for i in tri]
        us = [c[0] for c in corners]
        if max(us) - min(us) > 0.5:
            corners = [(u + 1.0 if u < 0.5 else u, v) for (u, v) in corners]
        idx = []
        for u, v in corners:
            uvs.append([round(u, 6), round(v, 6)])
            idx.append(len(uvs) - 1)
        face_uvs.append(idx)
    mesh.uvs = uvs
    mesh.face_uvs = face_uvs
    return mesh


def make_anim_curve(frames, fps, positions=None, rotations=None):
    """Build a 24-key style animation curve fixture (list of dicts)."""
    out = []
    for i in range(frames):
        t = i / float(fps)
        out.append(
            {
                "frame": i + 1,
                "time": round(t, 6),
                "location": positions[i] if positions else [0.0, 0.0, 0.0],
                "rotation_euler": rotations[i] if rotations else [0.0, 0.0, 0.0],
            }
        )
    return out
