# Offline Workstation Recovery Runbook

Procedures for the failure modes this Skill Tool is designed to survive. Each step is
executable with the bundled files and no network access.

## 0. First response

```sh
python tools-o3d.py doctor
python tools-o3d.py capabilities
python tools-o3d.py fixture --dir /tmp/o3d-smoke --force
python tools-o3d.py mesh inspect --mesh /tmp/o3d-smoke/fixture-cube.obj
```

If those four commands succeed, the runtime is healthy and the fault is in the project data.

## 1. The DCC will not launch

1. Note the exact version in `asset-offline-install-manifest.json`.
2. Verify the installer digest: `python tools-o3d.py install verify --manifest <manifest> --base-dir <bundle>`.
   A mismatch is a release blocker; re-copy the artifact from the staging machine.
3. Launch with a clean preferences profile (move the config folder aside, do not delete it).
4. If only add-ons fail, disable all add-ons, then re-enable one at a time in the order listed
   in the manifest; record the first failure.

## 2. A render dies from memory

```sh
python tools-o3d.py plan vram --triangles <count> --texture 4096x4096x3=albedo --samples 256 --device-gb <gb>
```

Apply the returned recommendation in this order: lower samples, split the scene into layers,
reduce texture resolution, then decimate background geometry with `mesh decimate`.

## 3. Scratch disk fills during simulation

```sh
python tools-o3d.py plan disk --frames <frames> --frame-mb <mb> --available-gb <free>
```

Delete reviewed proxy caches first, keep at most the retained cache versions, and move the
cache directory to another volume before re-running.

## 4. A mesh arrives broken from a vendor

```sh
python tools-o3d.py mesh inspect --mesh broken.obj --uv
python tools-o3d.py mesh repair --mesh broken.obj --fill --out repaired.obj
python tools-o3d.py mesh validate --mesh repaired.obj
```

If the gate still reports `nonmanifold_edges`, do not ship the repair; rebuild from the source
or request a clean export. Never patch a mesh with a boolean to hide a broken region.

## 5. Animation pops after a rig change

```sh
python tools-o3d.py anim gate --curves curves.json --out gate.json
python tools-o3d.py anim reduce --keys curves.json --tolerance 0.01 --out reduced.json
```

`jitter_or_pop` findings name the curve and the per-frame delta; compare the flagged frames
against the last approved take before touching the rig.

## 6. Mocap is scaled wrong after retarget

```sh
python tools-o3d.py mocap inspect --bvh capture.bvh
python tools-o3d.py mocap retarget --source Hips:95,Spine:12 --target Hips:104,Spine:14
```

The returned scale must be applied to translations only; rotations are copied unchanged. If
the character still floats, check the up axis before changing the scale again.

## 7. A delivery is rejected by the recipient

Run the package gate locally with the recipient's profile:

```sh
python tools-o3d.py validate package --dir 15-delivery --profile recipient-profile.json
python tools-o3d.py hash --file 15-delivery/*.obj --out checksums.json
```

Send the gate JSON with the delivery so the failure is reproducible instead of conversational.

## 8. Provenance or licence challenge

```sh
python tools-o3d.py license audit --assets assets.json
python tools-o3d.py license consent --records consent.json
python tools-o3d.py license provenance --file <generated-output> --model <name> --model-version <v> --seed <n>
```

If a record is missing, the asset is blocked from release until it is produced. Do not infer
permissions from a license name alone.

## 9. Corrupted or truncated capture / output files

Truncated BVH, PNG, EXR and OBJ inputs fail with an explicit error instead of producing partial
output. Re-copy from the archive, then verify the digest against the manifest. If the archive
copy also fails, treat the artifact as lost and rebuild from the last approved version.

## 10. Rollback to the last passing baseline

1. Record the current gate JSONs.
2. Restore the previous application version from the bundle manifest.
3. Re-run `doctor`, the fixtures, and the project gates.
4. Compare the gate JSONs to the last passing set; any regression blocks the update.
