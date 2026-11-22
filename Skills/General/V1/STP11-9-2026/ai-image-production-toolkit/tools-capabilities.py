"""Capability registry for the AI Image Production Toolkit.

Maps every skill in the source catalog (23 categories / 184 skills) to a runnable
handler, a machine-readable parameter contract and a risk classification.

The catalog itself lives in ``asset-capability-catalog.json`` so that the
registry stays in sync with the source document.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from tools_core import load_asset  # type: ignore  # noqa: F401  (loaded via importlib)

CATALOG_ASSET = "asset-capability-catalog.json"


def P(name: str, type_: str, desc: str, required: bool = False,
      default: Any = None, choices: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"name": name, "type": type_, "required": required, "default": default,
            "description": desc, "choices": choices}


def brief_params() -> List[Dict[str, Any]]:
    return [
        P("brief", "string", "What the image should communicate (free text).", True),
        P("subject", "string", "Primary subject of the image."),
        P("style", "string", "Target visual direction or style family."),
        P("audience", "string", "Intended audience.", default="general", choices=["general", "technical", "executive", "youth", "academic"]),
        P("model", "string", "Target model family (sd15, sd21, sdxl, sd3, flux, pony).", default="sdxl"),
    ]


def prompt_params() -> List[Dict[str, Any]]:
    return [
        P("prompt", "string", "Positive prompt text.", True),
        P("negative", "string", "Negative prompt text."),
        P("model", "string", "Target model family.", default="sdxl"),
        P("width", "integer", "Pixel width.", default=1024),
        P("height", "integer", "Pixel height.", default=1024),
        P("steps", "integer", "Sampling steps.", default=30),
        P("cfg", "number", "Classifier-free guidance scale.", default=7.0),
        P("sampler", "string", "Sampler name.", default="dpmpp_2m"),
        P("scheduler", "string", "Scheduler / sigma model.", default="karras"),
        P("seed", "integer", "Seed for reproducibility."),
    ]


def image_params(multi: bool = False) -> List[Dict[str, Any]]:
    out = [P("image", "path", "Path to the source image." if not multi else "Path to the first reference image.")]
    if multi:
        out.append(P("images", "path[]", "Additional reference images (repeatable)."))
    out.append(P("out_dir", "string", "Directory for generated artefacts.", default="output"))
    return out


def dir_params() -> List[Dict[str, Any]]:
    return [
        P("path", "path", "Directory or file to inspect."),
        P("out_dir", "string", "Directory for generated artefacts.", default="output"),
    ]


def text_params() -> List[Dict[str, Any]]:
    return [
        P("text", "string", "Primary input text.", True),
        P("out_dir", "string", "Directory for generated artefacts.", default="output"),
    ]


SPECS: Dict[str, Dict[str, Any]] = {}


def register(cap_id: str, handler: str, kind: str, risk: str, summary: str,
             params: List[Dict[str, Any]]) -> None:
    SPECS[cap_id] = {"handler": handler, "kind": kind, "risk": risk,
                     "summary": summary, "params": params}


# ---------------------------------------------------------------------------
# Category 1 - Foundations of Generative Imaging
# ---------------------------------------------------------------------------
register("foundations.image-training-concepts-explainer", "creative:foundations_training_concepts",
         "explain", "safe", "Explain how image-text training teaches visual patterns without implying stored galleries.",
         [P("topic", "string", "Concept to explain.", default="training-objective"),
          P("audience", "string", "Audience level.", default="general", choices=["general", "technical", "expert"]),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("foundations.pixel-and-tensor-interpreter", "creative:pixel_tensor_interpreter",
         "compute", "safe", "Convert width/height/channels/bit depth into tensor shapes, memory and normalisation specs.",
         [P("width", "integer", "Pixel width.", True),
          P("height", "integer", "Pixel height.", True),
          P("channels", "integer", "Colour channels.", default=3, choices=[1, 3, 4]),
          P("bit_depth", "integer", "Bits per channel.", default=8, choices=[8, 16]),
          P("latent_divisor", "integer", "VAE spatial downsample factor.", default=8),
          P("batch_size", "integer", "Batch size.", default=1)])
register("foundations.latent-space-navigator", "creative:latent_space_navigator",
         "compute", "safe", "Interpolate, combine and search a concept vector space with real arithmetic.",
         [P("concepts", "string[]", "Two or more concept names to navigate between.", True),
          P("operation", "string", "Navigation operation.", default="interpolate",
            choices=["interpolate", "add", "subtract", "nearest", "similarity"]),
          P("steps", "integer", "Interpolation steps.", default=5),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("foundations.diffusion-process-demonstrator", "media:diffusion_demonstrator",
         "artifact", "write", "Run a real forward-noising / reverse-denoising demonstration on a real image.",
         [P("image", "path", "Source image (optional; a synthetic test card is used when omitted)."),
          P("steps", "integer", "Diffusion steps in the schedule.", default=20),
          P("schedule", "string", "Noise schedule.", default="linear", choices=["linear", "cosine", "quadratic"]),
          P("out_dir", "string", "Directory for generated artefacts.", default="output"),
          P("keep_frames", "integer", "How many intermediate frames to write.", default=6)])
register("foundations.prompt-encoding-specialist", "creative:prompt_encoding_specialist",
         "analysis", "safe", "Tokenise a prompt, show attention budget, truncation risk and weighting effects.",
         prompt_params())
register("foundations.cross-attention-visualizer", "creative:cross_attention_visualizer",
         "analysis", "safe", "Bind prompt tokens to image regions and flag weak or ambiguous bindings.",
         [P("prompt", "string", "Prompt to analyse.", True),
          P("regions", "string", "Region layout to bind against.", default="thirds"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("foundations.generative-model-comparator", "creative:model_comparator",
         "analysis", "safe", "Compare model families on quality, speed, controllability, hardware and failure modes.",
         [P("priority", "string", "What matters most for the task.", default="quality",
            choices=["quality", "speed", "controllability", "hardware", "typography", "photorealism"]),
          P("models", "string[]", "Model families to compare (default: all)."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("foundations.image-synthesis-glossary-builder", "creative:glossary_builder",
         "artifact", "write", "Build an audience-aware glossary of image-synthesis terminology.",
         [P("terms", "string[]", "Terms to define (default: curated core set)."),
          P("audience", "string", "Audience level.", default="general", choices=["general", "technical", "expert"]),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 2 - Prompt Design and Creative Direction
# ---------------------------------------------------------------------------
register("prompt-design.prompt-architect", "creative:prompt_architect",
         "artifact", "write", "Turn a visual goal into a structured prompt covering subject, light, lens, palette and exclusions.",
         brief_params() + [P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("prompt-design.negative-prompt-engineer", "creative:negative_prompt_engineer",
         "artifact", "write", "Build concise negative constraints for likely artifacts without killing detail.",
         [P("target", "string", "Artifact class to suppress (e.g. hands, text, watermark).", default="general"),
          P("scene", "string", "Description of the intended image."),
          P("strictness", "string", "Suppression strength.", default="balanced", choices=["light", "balanced", "strong"]),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("prompt-design.prompt-debugger", "creative:prompt_debugger",
         "analysis", "safe", "Diagnose ignored objects, merged concepts and style drift, then vary one variable at a time.",
         [P("prompt", "string", "Failing prompt.", True),
          P("observed", "string", "What the image actually showed."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("prompt-design.prompt-variant-generator", "creative:prompt_variant_generator",
         "artifact", "write", "Produce controlled prompt families that vary only selected dimensions.",
         [P("prompt", "string", "Base prompt.", True),
          P("dimensions", "string[]", "Dimensions to vary.", default=["camera_angle", "era", "weather"]),
          P("variants_per_dimension", "integer", "Variants per dimension.", default=3),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("prompt-design.art-direction-translator", "creative:art_direction_translator",
         "artifact", "write", "Translate mood boards, brand language or briefs into concrete visual attributes.",
         [P("mood", "string", "Mood or brand language.", True),
          P("medium", "string", "Target medium.", default="photography"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("prompt-design.prompt-weighting-specialist", "creative:prompt_weighting_specialist",
         "artifact", "write", "Apply token emphasis, regional separation and model-specific weighting syntax safely.",
         [P("prompt", "string", "Prompt to weight.", True),
          P("syntax", "string", "Weighting dialect.", default="a1111", choices=["a1111", "comfyui", "plain"]),
          P("max_weight", "number", "Refuse weights above this value.", default=1.4),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("prompt-design.seed-and-reproducibility-manager", "creative:reproducibility_manager",
         "artifact", "write", "Build, verify and hash reproducible run manifests for image generation.",
         [P("prompt", "string", "Prompt recorded in the manifest.", required=False),
          P("model", "string", "Model identifier / checkpoint.", default="sdxl-base"),
          P("seed", "integer", "Seed value.", default=42),
          P("manifest", "path", "Existing manifest to verify (optional)."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("prompt-design.prompt-library-curator", "creative:prompt_library_curator",
         "artifact", "write", "Curate a searchable prompt library with tags, model compatibility and failure notes.",
         [P("library", "path", "Existing library JSON to update (optional)."),
          P("query", "string", "Filter expression, e.g. 'product AND sdxl'."),
          P("entries", "string[]", "New entries as 'title|prompt|tags' strings."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 3 - Stable Diffusion Core Operations
# ---------------------------------------------------------------------------
register("stable-diffusion.stable-diffusion-setup-guide", "production:sd_setup_guide",
         "plan", "safe", "Plan a local or hosted Stable Diffusion installation with hardware and update policy.",
         [P("gpu_vram_gb", "number", "GPU VRAM in GB.", default=12),
          P("interface", "string", "Target interface.", default="comfyui", choices=["comfyui", "a1111", "forge", "diffusers", "api"]),
          P("os", "string", "Operating system.", default="linux"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("stable-diffusion.checkpoint-selection-advisor", "production:checkpoint_advisor",
         "analysis", "safe", "Match checkpoints to realism, illustration, product, typography or speed requirements.",
         [P("goal", "string", "Primary goal.", default="photorealism",
            choices=["photorealism", "illustration", "anime", "product", "architecture", "typography", "speed"]),
          P("budget", "string", "Compute budget.", default="consumer", choices=["consumer", "prosumer", "workstation", "cloud"]),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("stable-diffusion.sampler-and-scheduler-tuner", "production:sampler_tuner",
         "plan", "write", "Select samplers, schedulers, steps and guidance with a reproducible comparison grid.",
         [P("model", "string", "Model family.", default="sdxl"),
          P("goal", "string", "Optimisation goal.", default="quality", choices=["quality", "speed", "anatomy", "style"]),
          P("sampler", "string", "Sampler to evaluate.", default="dpmpp_2m"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("stable-diffusion.vae-manager", "production:vae_manager",
         "diagnose", "safe", "Choose and troubleshoot VAEs including black-image and colour-shift diagnosis.",
         [P("image", "path", "Image to diagnose (optional)."),
          P("symptom", "string", "Symptom to diagnose.", default="black_image"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("stable-diffusion.sdxl-workflow-specialist", "production:sdxl_workflow",
         "plan", "write", "Build SDXL base/refiner workflows with resolution, conditioning and memory control.",
         [P("aspect", "string", "Target aspect ratio.", default="1:1", choices=["1:1", "3:2", "2:3", "16:9", "9:16"]),
          P("refiner", "boolean", "Include a refiner pass.", default=False),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("stable-diffusion.stable-diffusion-3-workflow-specialist", "production:sd3_workflow",
         "plan", "write", "Design SD3-class workflows with encoder, MMDiT and memory considerations.",
         [P("variant", "string", "SD3 variant.", default="sd3-medium", choices=["sd3-medium", "sd3-large", "sd3.5-medium", "sd3.5-large"]),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("stable-diffusion.webui-workflow-builder", "production:webui_workflow",
         "artifact", "write", "Generate reproducible Automatic1111/Forge parameter sets, presets and batch scripts.",
         [P("prompt", "string", "Positive prompt.", True),
          P("negative", "string", "Negative prompt."),
          P("model", "string", "Checkpoint name.", default="sdxl-base"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("stable-diffusion.comfyui-graph-designer", "production:comfyui_graph",
         "artifact", "write", "Build modular ComfyUI node graphs for generation, control, inpainting and upscaling.",
         [P("template", "string", "Graph template.", default="txt2img",
            choices=["txt2img", "img2img", "inpaint", "controlnet", "upscale", "hires-fix"]),
          P("prompt", "string", "Prompt embedded in the graph.", default="a cinematic photograph of a quiet harbour at dawn"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 4 - Diffusion Control and Conditioning
# ---------------------------------------------------------------------------
register("control-conditioning.controlnet-orchestrator", "production:controlnet_orchestrator",
         "plan", "write", "Choose and combine edge, depth, pose, line-art, normal, tile and scribble controls.",
         [P("goal", "string", "Control goal.", default="structure"),
          P("controls", "string[]", "Control types to combine."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("control-conditioning.pose-conditioning-specialist", "production:pose_specialist",
         "analyze", "safe", "Validate a 2D pose skeleton for limb integrity, laterality and plausibility.",
         [P("pose", "path", "JSON file with pose keypoints."),
          P("joints", "string", "Joint order (comma separated).", default="nose,neck,lsho,lelb,lwri,rsho,relb,rwri,lsho,rsho,lhip,rhip,lknee,rknee,lank,rank"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("control-conditioning.depth-guided-composer", "production:depth_composer",
         "analyze", "safe", "Plan depth-guided restyling and validate a depth map against its source.",
         [P("image", "path", "Source image."),
          P("depth", "path", "Depth map image."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("control-conditioning.edge-and-line-art-controller", "production:edge_controller",
         "analyze", "safe", "Validate edge/line-art maps for alignment with the source and over-constraining.",
         [P("image", "path", "Source image."),
          P("edges", "path", "Edge or line-art map."),
          P("mode", "string", "Edge family.", default="canny", choices=["canny", "hed", "softedge", "lineart", "scribble"]),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("control-conditioning.segmentation-guided-generator", "production:segmentation_generator",
         "artifact", "write", "Turn a semantic segmentation map into per-region generation instructions.",
         [P("segmentation", "path", "Segmentation map image."),
          P("classes", "string", "Class id to label mapping as 'id:label,id:label'."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("control-conditioning.ip-adapter-specialist", "production:ip_adapter_specialist",
         "plan", "write", "Weight reference-image embeddings by role (style, face, object, composition).",
         [P("references", "path[]", "Reference images."),
          P("role", "string", "Primary reference role.", default="style",
            choices=["style", "face", "object", "composition", "palette"]),
          P("prompt_weight", "number", "Relative prompt weight.", default=1.0),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("control-conditioning.regional-prompting-composer", "production:regional_prompting",
         "artifact", "write", "Define spatial regions with distinct subjects, styles and constraints.",
         [P("regions", "string", "Region spec 'x,y,w,h:prompt' separated by ';'."),
          P("layout", "string", "Layout helper.", default="quadrants"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("control-conditioning.multi-control-conflict-resolver", "production:control_conflict_resolver",
         "analysis", "safe", "Detect competing conditioning signals and rebalance strengths and denoise windows.",
         [P("controls", "string", "Control spec 'name:type:strength:start:end' separated by ';'."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 5 - Text-to-Image Creation
# ---------------------------------------------------------------------------
_T2I = [
    P("subject", "string", "What to depict.", True),
    P("deliverable", "string", "Deliverable flavour (pack shot, sheet, hero, scene)."),
    P("medium", "string", "Target medium.", default="photography"),
    P("out_dir", "string", "Directory for generated artefacts.", default="output"),
]
register("text-to-image.concept-art-generator", "creative:concept_art_generator", "artifact", "write",
         "Create concept-art packages with iteration sheets and production-ready visual notes.", _T2I)
register("text-to-image.photorealistic-scene-creator", "creative:photorealistic_scene", "artifact", "write",
         "Specify lenses, exposure, lighting, materials and natural imperfections for realism.", _T2I)
register("text-to-image.illustration-style-creator", "creative:illustration_style", "artifact", "write",
         "Generate illustration directions without imitating living artists.", _T2I)
register("text-to-image.product-visualization-maker", "creative:product_visualization", "artifact", "write",
         "Create product pack shots, exploded views and material variants with branding safeguards.", _T2I)
register("text-to-image.architectural-visualization-maker", "creative:architectural_visualization", "artifact", "write",
         "Generate exterior and interior concepts with coherent perspective and daylight.", _T2I)
register("text-to-image.food-and-beverage-visual-maker", "creative:food_visual", "artifact", "write",
         "Create physically plausible food imagery with texture, styling and commercial light.", _T2I)
register("text-to-image.fashion-concept-generator", "creative:fashion_concept", "artifact", "write",
         "Develop garments, textiles, silhouettes and colorways that respect construction logic.", _T2I)
register("text-to-image.abstract-visual-synthesizer", "creative:abstract_synthesizer", "artifact", "write",
         "Create non-representational images using controlled colour, rhythm, geometry and depth.", _T2I)

# ---------------------------------------------------------------------------
# Category 6 - Single-Image Editing
# ---------------------------------------------------------------------------
register("single-image-editing.inpainting-specialist", "media:inpainting_specialist", "plan", "write",
         "Replace, add or repair regions with masks, denoising control and edge blending.",
         [P("image", "path", "Source image."), P("mask", "path", "Mask image (white = regenerate)."),
          P("instruction", "string", "What to put in the masked region.", True),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("single-image-editing.outpainting-specialist", "media:outpainting_specialist", "plan", "write",
         "Extend beyond the original borders while preserving perspective, light and texture.",
         [P("image", "path", "Source image."), P("expand", "string", "Expansion as 'left,top,right,bottom' pixels or 'scale:1.5'."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("single-image-editing.object-removal-editor", "media:object_removal_editor", "plan", "write",
         "Remove unwanted objects and reconstruct plausible backgrounds.",
         [P("image", "path", "Source image."), P("mask", "path", "Mask covering the object to remove."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("single-image-editing.object-addition-editor", "media:object_addition_editor", "plan", "write",
         "Insert new subjects with consistent scale, contact shadow, reflection and focus.",
         [P("image", "path", "Scene image."), P("object", "string", "Object to insert.", True),
          P("position", "string", "Placement hint, e.g. 'lower-left on the table'."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("single-image-editing.background-replacement-editor", "media:background_replacement_editor", "plan", "write",
         "Separate foregrounds and replace scenes with matched edge detail and light.",
         [P("image", "path", "Source image."), P("matte", "path", "Alpha matte."),
          P("new_background", "string", "Description of the new background.", True),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("single-image-editing.relighting-specialist", "media:relighting_specialist", "plan", "write",
         "Change key, fill, rim, direction, softness and colour temperature; optionally apply a tone approximation.",
         [P("image", "path", "Source image."), P("direction", "string", "Key light direction.", default="upper-left"),
          P("temperature", "string", "Colour temperature.", default="5600K"),
          P("apply", "boolean", "Apply the deterministic tone approximation to a copy.", default=False),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("single-image-editing.expression-and-gaze-editor", "media:expression_gaze_editor", "plan", "write",
         "Make controlled expression or gaze changes while preserving identity and anatomy.",
         [P("image", "path", "Source image."), P("expression", "string", "Target expression.", True),
          P("gaze", "string", "Target gaze direction.", default="camera"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("single-image-editing.image-repair-and-restoration-editor", "media:restoration_editor", "transform", "write",
         "Conservatively repair scratches, compression damage and faded regions; applies a real restoration pass.",
         [P("image", "path", "Source image."), P("strength", "number", "Restoration strength 0-1.", default=0.4),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 7 - Multi-Image Editing and Composition
# ---------------------------------------------------------------------------
register("multi-image-composition.multi-reference-composer", "creative:multi_reference_composer", "plan", "write",
         "Combine subjects, styles, poses, products and locations from several references with explicit roles.",
         [P("references", "path[]", "Reference images.", True),
          P("roles", "string", "Role assignment 'file:role' separated by ';'."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("multi-image-composition.identity-consistency-manager", "media:identity_consistency_manager", "analyze", "safe",
         "Measure identity drift across references and design a consistency workflow.",
         [P("images", "path[]", "Images to compare.", True), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("multi-image-composition.style-consistency-manager", "media:style_consistency_manager", "analyze", "safe",
         "Compare palette, contrast and texture across unrelated scenes to keep a look consistent.",
         [P("images", "path[]", "Images to compare.", True), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("multi-image-composition.photomontage-integrator", "creative:photomontage_integrator", "plan", "write",
         "Blend photographs with perspective matching, masking, grading, grain and depth cues.",
         [P("images", "path[]", "Photographs to blend.", True), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("multi-image-composition.contact-sheet-builder", "media:contact_sheet_builder", "artifact", "write",
         "Build a labelled contact-sheet grid from images or from a specification sheet.",
         [P("images", "path[]", "Images to lay out."),
          P("spec", "path", "Specification sheet JSON to render instead of images."),
          P("columns", "integer", "Grid columns.", default=4),
          P("cell_width", "integer", "Cell width in pixels.", default=320),
          P("label", "string", "Grid label.", default="variation sheet"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("multi-image-composition.before-and-after-editor", "media:before_after_editor", "artifact", "write",
         "Build an aligned before/after comparison with measured change statistics.",
         [P("before", "path", "Before image."), P("after", "path", "After image."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("multi-image-composition.visual-asset-family-generator", "creative:visual_asset_family", "artifact", "write",
         "Produce coordinated hero, banner, thumbnail, card and social crops from one campaign direction.",
         [P("campaign", "string", "Campaign direction.", True), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("multi-image-composition.reference-conflict-resolver", "creative:reference_conflict_resolver", "analysis", "safe",
         "Detect contradictions among references and prioritise which attribute wins.",
         [P("references", "string", "Reference attribute table as 'file:attr=value' separated by ';'."), P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 8 - Masking, Selections, and Compositing
# ---------------------------------------------------------------------------
register("masking-compositing.precision-mask-generator", "media:precision_mask_generator", "transform", "write",
         "Build clean masks for hair, fur, glass, smoke and foliage, optionally feathering a supplied mask.",
         [P("mask", "path", "Binary mask to refine (white = keep)."),
          P("material", "string", "Material class.", default="hair"),
          P("feather", "integer", "Feather radius in pixels.", default=6),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("masking-compositing.semantic-segmentation-editor", "media:semantic_segmentation_editor", "analyze", "safe",
         "Validate a segmentation map and convert it into production-ready mask definitions.",
         [P("segmentation", "path", "Segmentation map."), P("classes", "string", "Class mapping 'id:label'."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("masking-compositing.alpha-matting-specialist", "media:alpha_matting_specialist", "analyze", "safe",
         "Measure cutout edge contamination, spill and halo risk and score matte quality.",
         [P("image", "path", "Cutout image with alpha."), P("background", "path", "Original background (optional)."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("masking-compositing.layered-composite-builder", "creative:layered_composite_builder", "artifact", "write",
         "Assemble foreground, midground, background, effects and grading layers with blend logic.",
         [P("layers", "string", "Layer spec 'name:path:blend:opacity' separated by ';'."), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("masking-compositing.shadow-and-reflection-composer", "creative:shadow_reflection_composer", "compute", "write",
         "Compute cast-shadow and reflection geometry from light direction and subject bounds.",
         [P("subject_width", "number", "Subject width in pixels.", default=400),
          P("subject_height", "number", "Subject height in pixels.", default=600),
          P("light_direction", "string", "Light azimuth in degrees.", default="135"),
          P("elevation", "number", "Light elevation in degrees.", default=45),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("masking-compositing.occlusion-planner", "creative:occlusion_planner", "plan", "write",
         "Determine front-back relationships and mask overlaps for natural subject insertion.",
         [P("subjects", "string", "Subject spec 'name:x,y,w,h,depth' separated by ';'."), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("masking-compositing.edge-decontamination-specialist", "media:edge_decontamination_specialist", "transform", "write",
         "Remove colour spill, halos and background contamination from a cutout edge.",
         [P("image", "path", "Cutout image."), P("spill", "string", "Spill colour to suppress.", default="green"),
          P("amount", "number", "Suppression strength 0-1.", default=0.6),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("masking-compositing.blend-mode-advisor", "creative:blend_mode_advisor", "analysis", "safe",
         "Choose a non-destructive blend mode and masking strategy for a composite.",
         [P("foreground", "string", "Foreground description."), P("background", "string", "Background description."),
          P("intent", "string", "Blend intent.", default="natural", choices=["natural", "glow", "shadow", "grade", "texture"]),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 9 - Upscaling, Enhancement, and Restoration
# ---------------------------------------------------------------------------
register("upscaling-restoration.ai-upscaling-specialist", "production:upscaling_advisor", "plan", "write",
         "Choose latent, diffusion, ESRGAN-class or face-aware upscaling from source quality and target size.",
         [P("image", "path", "Source image (optional)."), P("target_width", "integer", "Target width.", default=2048),
          P("target_height", "integer", "Target height.", default=2048),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("upscaling-restoration.high-resolution-fix-tuner", "media:hires_fix_tuner", "transform", "write",
         "Plan and perform a tiled second-pass upscale with overlap and seam control.",
         [P("image", "path", "Source image."), P("scale", "number", "Upscale factor.", default=2.0),
          P("tile", "integer", "Tile size in pixels.", default=512), P("overlap", "integer", "Tile overlap.", default=64),
          P("apply", "boolean", "Actually perform the tiled upscale.", default=True),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("upscaling-restoration.detail-recovery-specialist", "production:detail_recovery", "plan", "write",
         "Recover credible fine texture without inventing destructive facial, logo or material detail.",
         [P("image", "path", "Source image (optional)."), P("subject", "string", "Subject type.", default="product"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("upscaling-restoration.face-restoration-controller", "production:face_restoration", "plan", "write",
         "Choose face-restoration strength and guard against plastic texture and identity drift.",
         [P("subject", "string", "Subject type.", default="portrait"),
          P("goal", "string", "Preservation vs beautification.", default="preservation", choices=["preservation", "balanced", "beautification"]),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("upscaling-restoration.noise-and-grain-manager", "media:noise_grain_manager", "transform", "write",
         "Estimate noise, denoise conservatively and add coherent seeded grain.",
         [P("image", "path", "Source image."), P("denoise", "number", "Denoise strength 0-1.", default=0.3),
          P("grain", "number", "Grain amount 0-1 (added after denoise).", default=0.0),
          P("seed", "integer", "Grain seed.", default=7), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("upscaling-restoration.deblur-and-sharpening-specialist", "media:deblur_sharpening", "transform", "write",
         "Estimate blur radius and apply artefact-aware sharpening.",
         [P("image", "path", "Source image."), P("amount", "number", "Sharpening amount 0-2.", default=0.6),
          P("apply", "boolean", "Write the sharpened copy.", default=True),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("upscaling-restoration.old-photo-restoration-specialist", "media:old_photo_restoration", "transform", "write",
         "Detect scratches and repair aged photos while separating restoration from speculation.",
         [P("image", "path", "Source image."), P("strength", "number", "Repair strength 0-1.", default=0.35),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("upscaling-restoration.print-resolution-planner", "production:print_resolution_planner", "compute", "safe",
         "Calculate pixel dimensions, DPI, bleed, crop and viewing distance for print targets.",
         [P("width_px", "integer", "Pixel width.", True), P("height_px", "integer", "Pixel height.", True),
          P("dpi", "integer", "Target print resolution.", default=300),
          P("bleed_mm", "number", "Bleed in millimetres.", default=3.0),
          P("target_inches", "string", "Desired print size as 'WxH' inches (optional)."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 10 - Color, Lighting, and Finishing
# ---------------------------------------------------------------------------
register("color-lighting.color-grading-specialist", "media:color_grading_specialist", "transform", "write",
         "Build a tone curve and apply a deterministic colour grade with protected hues.",
         [P("image", "path", "Source image."), P("mood", "string", "Target mood.", default="cinematic_teal_orange"),
          P("strength", "number", "Grade strength 0-1.", default=0.5),
          P("apply", "boolean", "Write the graded copy.", default=True),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("color-lighting.palette-transfer-editor", "media:palette_transfer_editor", "transform", "write",
         "Transfer colour relationships from a reference while protecting skin, neutrals and brand colours.",
         [P("image", "path", "Target image."), P("reference", "path", "Reference image."),
          P("protect", "string", "Protected hue ranges as 'name:hue_start-hue_end' separated by ';'."),
          P("strength", "number", "Transfer strength 0-1.", default=0.6),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("color-lighting.lighting-continuity-auditor", "media:lighting_continuity_auditor", "analyze", "safe",
         "Audit shadow direction, softness, intensity and reflections across a composite.",
         [P("image", "path", "Composite image."), P("expected_direction", "string", "Expected key direction.", default="upper-left"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("color-lighting.day-to-night-converter", "media:day_to_night_converter", "transform", "write",
         "Convert daylight to night with plausible exposure, ambient shift and practical highlights.",
         [P("image", "path", "Source image."), P("strength", "number", "Night conversion strength 0-1.", default=0.7),
          P("apply", "boolean", "Write the converted copy.", default=True),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("color-lighting.season-and-weather-transformer", "creative:season_weather_transformer", "plan", "write",
         "Change rain, snow, fog, season and foliage while preserving scene structure.",
         [P("image", "path", "Source image (optional)."), P("weather", "string", "Weather target.", default="light_rain"),
          P("season", "string", "Season target.", default="winter"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("color-lighting.cinematic-look-designer", "media:cinematic_look_designer", "transform", "write",
         "Design a filmic look with contrast, halation, bloom, grain and lens behaviour, then apply it.",
         [P("image", "path", "Source image."), P("look", "string", "Look name.", default="teal_orange"),
          P("grain", "number", "Grain amount.", default=0.15),
          P("apply", "boolean", "Write the graded copy.", default=True),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("color-lighting.hdr-and-dynamic-range-specialist", "media:hdr_specialist", "transform", "write",
         "Measure clipping and merge bracketed exposures into a balanced high-range result.",
         [P("image", "path", "Primary exposure."),
          P("exposures", "path[]", "Additional exposure images (darkest to brightest)."),
          P("apply", "boolean", "Write the merged copy.", default=True),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("color-lighting.output-color-manager", "production:output_color_manager", "analyze", "safe",
         "Prepare sRGB, P3, CMYK-oriented, HDR and print-preview outputs with explicit conversion guidance.",
         [P("image", "path", "Source image."), P("target", "string", "Target colour space.", default="srgb",
            choices=["srgb", "display_p3", "cmyk_oriented", "hdr_pq", "print_preview"]),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 11 - Typography, Logos, and Graphic Design
# ---------------------------------------------------------------------------
register("typography-design.image-typography-planner", "creative:typography_planner", "artifact", "write",
         "Plan type hierarchy and placement and render a deterministic SVG layout with contrast checks.",
         [P("headline", "string", "Headline text.", True),
          P("subhead", "string", "Subhead text."),
          P("width", "integer", "Canvas width.", default=1200), P("height", "integer", "Canvas height.", default=800),
          P("background", "string", "Background hex colour.", default="#101014"),
          P("text_color", "string", "Text hex colour.", default="#f5f5f7"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("typography-design.ai-text-artifact-corrector", "creative:text_artifact_corrector", "analysis", "safe",
         "Detect garbled generated lettering and replace it with accurate deterministic typography.",
         [P("image", "path", "Image containing generated text (optional)."),
          P("text", "string", "Correct text content."), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("typography-design.logo-concept-generator", "creative:logo_concept_generator", "artifact", "write",
         "Explore symbol, wordmark, monogram and lockup directions with simplicity and distinctiveness checks.",
         [P("brand", "string", "Brand or subject.", True), P("industry", "string", "Industry context.", default="general"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("typography-design.poster-and-key-art-designer", "creative:poster_designer", "artifact", "write",
         "Design poster and key-art layouts with title, billing and call-to-action zones.",
         [P("title", "string", "Title text.", True), P("subtitle", "string", "Subtitle / billing."),
          P("width", "integer", "Canvas width.", default=1200), P("height", "integer", "Canvas height.", default=1600),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("typography-design.social-graphic-builder", "creative:social_graphic_builder", "artifact", "write",
         "Produce platform-ready square, portrait, landscape, story and thumbnail designs with crop-safe focal points.",
         [P("headline", "string", "Headline text.", True), P("platform", "string", "Platform preset.", default="instagram_post"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("typography-design.packaging-artwork-conceptualizer", "creative:packaging_conceptualizer", "plan", "write",
         "Map label and package concepts to dieline constraints, panels, barcodes and legal copy.",
         [P("product", "string", "Product description.", True), P("dieline", "string", "Dieline name.", default="sleek_carton_120x60"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("typography-design.infographic-visual-asset-maker", "creative:infographic_asset_maker", "artifact", "write",
         "Create icon and diagram components while keeping factual text out of the image model.",
         [P("topic", "string", "Infographic topic.", True), P("items", "integer", "Number of data items.", default=5),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("typography-design.brand-template-adapter", "creative:brand_template_adapter", "artifact", "write",
         "Apply approved brand colours, fonts, spacing and clear space to a repeatable asset.",
         [P("brand", "string", "Brand name.", True), P("headline", "string", "Headline text."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 12 - Characters, Faces, and People
# ---------------------------------------------------------------------------
register("characters-people.character-design-specialist", "creative:character_design_specialist", "artifact", "write",
         "Define silhouette, proportions, costume, props, palette and turnaround for an original character.",
         [P("character", "string", "Character concept.", True), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("characters-people.character-consistency-engineer", "creative:character_consistency_engineer", "plan", "write",
         "Design a reference-sheet and anchor-prompt workflow that keeps a character consistent across scenes.",
         [P("character", "string", "Character concept.", True), P("scenes", "integer", "Number of scenes to plan.", default=5),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("characters-people.portrait-generator", "creative:portrait_generator", "artifact", "write",
         "Create portrait specifications with framing, lens, pose, light, background and retouching constraints.",
         [P("subject", "string", "Subject description.", True), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("characters-people.group-composition-specialist", "creative:group_composition_specialist", "compute", "write",
         "Lay out multiple people with distinct identities, spacing, gaze and overlap using real geometry.",
         [P("people", "integer", "Number of people.", default=3), P("frame", "string", "Frame size 'WxH'.", default="1536x1024"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("characters-people.hands-and-anatomy-repair-specialist", "media:hands_anatomy_repair", "plan", "write",
         "Detect and repair finger, joint, limb and perspective errors with masked regeneration plans.",
         [P("image", "path", "Image to inspect (optional)."), P("region", "string", "Region of interest.", default="hands"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("characters-people.wardrobe-and-costume-editor", "creative:wardrobe_costume_editor", "plan", "write",
         "Change clothing, materials, accessories and era while preserving pose and body continuity.",
         [P("image", "path", "Source image (optional)."), P("garment", "string", "Target garment or uniform.", True),
          P("era", "string", "Historical era.", default="contemporary"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("characters-people.hair-and-grooming-editor", "creative:hair_grooming_editor", "plan", "write",
         "Modify hairstyle, length, texture or facial hair with accurate edges and identity preservation.",
         [P("image", "path", "Source image (optional)."), P("hair", "string", "Target hairstyle.", True),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("characters-people.ethical-portrait-workflow-auditor", "production:ethical_portrait_auditor", "audit", "safe",
         "Score consent, impersonation, sensitivity, minors and disclosure readiness before a portrait ships.",
         [P("subject", "string", "Portrait subject description.", True),
          P("consent_records", "path", "JSON consent record file (optional)."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 13 - Products, Commerce, and Advertising
# ---------------------------------------------------------------------------
register("commerce-advertising.e-commerce-image-producer", "production:ecommerce_producer", "plan", "write",
         "Define white-background, angle, detail, scale and shadow standards for product listings.",
         [P("product", "string", "Product description.", True), P("marketplace", "string", "Marketplace preset.", default="generic"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("commerce-advertising.virtual-product-photography-director", "creative:product_photo_director", "artifact", "write",
         "Plan studio sets, lenses, surfaces and light rigs and render a lighting diagram.",
         [P("product", "string", "Product description.", True), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("commerce-advertising.product-variant-generator", "creative:product_variant_generator", "artifact", "write",
         "Generate controlled colour, finish and material variants while keeping geometry fixed.",
         [P("product", "string", "Product description.", True), P("dimension", "string", "Variant dimension.", default="color"),
          P("count", "integer", "Variants to generate.", default=4),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("commerce-advertising.lifestyle-placement-specialist", "creative:lifestyle_placement", "plan", "write",
         "Place products in credible use contexts with correct scale, contact and reflections.",
         [P("product", "string", "Product description.", True), P("context", "string", "Usage context.", default="kitchen counter"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("commerce-advertising.advertising-concept-generator", "creative:advertising_concept", "artifact", "write",
         "Develop campaign territories, visual metaphors, hero shots and channel adaptations.",
         [P("brief", "string", "Campaign brief.", True), P("channels", "string", "Comma separated channels.", default="social,web"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("commerce-advertising.marketplace-compliance-reviewer", "media:marketplace_compliance", "audit", "safe",
         "Check image dimensions, background, overlays and badges against a marketplace policy.",
         [P("image", "path", "Image to review."), P("marketplace", "string", "Marketplace preset.", default="generic"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("commerce-advertising.catalog-consistency-auditor", "media:catalog_consistency_auditor", "audit", "safe",
         "Measure crop, angle, lighting, white point and colour deviations across a product folder.",
         [P("path", "path", "Folder of product images."), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("commerce-advertising.conversion-creative-variant-maker", "creative:conversion_variant_maker", "artifact", "write",
         "Generate testable ad alternatives that change exactly one hypothesis at a time.",
         [P("brief", "string", "Ad brief.", True), P("hypotheses", "string", "Comma separated hypotheses.", default="benefit_framing,focal_product,context"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 14 - Architecture, Interiors, and Spatial Design
# ---------------------------------------------------------------------------
register("architecture-interiors.interior-concept-visualizer", "creative:interior_concept_visualizer", "artifact", "write",
         "Translate a room brief into a scaled concept with circulation, furniture, materials and lighting.",
         [P("room", "string", "Room type.", default="living_room"), P("dimensions", "string", "Room size 'WxH' metres.", default="5.4x4.2"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("architecture-interiors.exterior-massing-visualizer", "creative:exterior_massing_visualizer", "artifact", "write",
         "Explore facade rhythm, massing, openings, landscape and daylight with an assumptions log.",
         [P("building", "string", "Building description.", True), P("storeys", "integer", "Number of storeys.", default=4),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("architecture-interiors.floor-plan-to-perspective-designer", "production:floor_plan_perspective", "compute", "write",
         "Derive camera setup and lens choice from plan geometry without inventing connections.",
         [P("width_m", "number", "Room width in metres.", True), P("depth_m", "number", "Room depth in metres.", True),
          P("height_m", "number", "Ceiling height in metres.", default=2.8),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("architecture-interiors.material-and-finish-variant-maker", "creative:material_variant_maker", "artifact", "write",
         "Swap floors, walls, cabinetry and metals while preserving geometry and light response.",
         [P("room", "string", "Space description.", True), P("surface", "string", "Surface to vary.", default="floor"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("architecture-interiors.furniture-staging-specialist", "creative:furniture_staging_specialist", "compute", "write",
         "Stage a room for function and balance while checking clearances and accessibility.",
         [P("room", "string", "Room type.", default="living_room"), P("dimensions", "string", "Room size 'WxH' metres.", default="5.4x4.2"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("architecture-interiors.landscape-visualization-creator", "creative:landscape_visualization", "plan", "write",
         "Create planting, hardscape, water and season concepts that respect maintenance reality.",
         [P("site", "string", "Site description.", True), P("climate", "string", "Climate zone.", default="temperate"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("architecture-interiors.real-estate-enhancement-editor", "creative:real_estate_enhancement", "plan", "write",
         "Improve exposure, declutter and verticals without concealing material defects.",
         [P("image", "path", "Source image (optional)."), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("architecture-interiors.spatial-consistency-auditor", "creative:spatial_consistency_auditor", "audit", "safe",
         "Audit perspective, vanishing points, door logic, scale and repeated elements.",
         [P("image", "path", "Image to audit (optional)."), P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 15 - Scientific, Medical, and Technical Imaging
# ---------------------------------------------------------------------------
register("scientific-technical.scientific-illustration-planner", "creative:scientific_illustration_planner", "artifact", "write",
         "Turn a verified mechanism into a labelled plan that separates evidence from interpretation.",
         [P("mechanism", "string", "Mechanism or structure.", True), P("labels", "string", "Known labels (comma separated)."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("scientific-technical.medical-visualization-safety-reviewer", "production:medical_safety_reviewer", "audit", "safe",
         "Review educational medical imagery for plausibility, privacy, labelling and non-diagnostic framing.",
         [P("description", "string", "What the image depicts.", True),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("scientific-technical.technical-cutaway-creator", "creative:technical_cutaway_creator", "artifact", "write",
         "Design exploded, cutaway and sectional concepts with consistent part relationships and callout space.",
         [P("assembly", "string", "Assembly or device.", True), P("parts", "string", "Known parts (comma separated)."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("scientific-technical.dataset-visualization-asset-maker", "creative:dataset_visualization_assets", "artifact", "write",
         "Create decorative assets around charts without altering or fabricating the underlying data.",
         [P("chart_type", "string", "Chart type.", default="bar"), P("items", "integer", "Number of series or points.", default=6),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("scientific-technical.microscopy-image-enhancement-guide", "production:microscopy_enhancement", "plan", "write",
         "Plan denoising, contrast and segmentation that preserve quantitative integrity.",
         [P("image", "path", "Microscopy image (optional)."), P("modality", "string", "Modality.", default="brightfield"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("scientific-technical.satellite-and-geospatial-image-stylist", "creative:geospatial_stylist", "plan", "write",
         "Style geospatial imagery while preserving scale, orientation and legend accuracy.",
         [P("image", "path", "Source imagery (optional)."), P("style", "string", "Styling target.", default="muted_cartographic"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("scientific-technical.patent-figure-preparation-guide", "creative:patent_figure_guide", "artifact", "write",
         "Convert concepts into monochrome line drawings with consistent numbering and contours.",
         [P("invention", "string", "Invention description.", True), P("views", "integer", "Number of views.", default=3),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("scientific-technical.technical-accuracy-auditor", "production:technical_accuracy_auditor", "audit", "safe",
         "Compare a technical image against specifications before release.",
         [P("image", "path", "Technical image (optional)."), P("spec", "string", "Specifications to check against.", True),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 16 - Training, Fine-Tuning, and Personalization
# ---------------------------------------------------------------------------
register("training-personalization.dataset-curation-specialist", "production:dataset_curation", "analyze", "write",
         "Analyse a caption dataset for coverage, duplication, balance and quality and emit a curated subset.",
         [P("dataset", "path", "JSONL or CSV dataset with a caption column."), P("caption_field", "string", "Caption column.", default="caption"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("training-personalization.captioning-pipeline-designer", "production:captioning_pipeline", "artifact", "write",
         "Design a consistent caption template with trigger terms, attributes and caption dropout.",
         [P("dataset", "path", "Dataset to profile (optional)."), P("caption_field", "string", "Caption column.", default="caption"),
          P("trigger", "string", "Trigger token for a learned concept."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("training-personalization.lora-training-specialist", "production:lora_training", "compute", "write",
         "Compute LoRA rank, alpha, learning rate, buckets, repeats and epochs from a dataset size.",
         [P("dataset", "path", "Dataset path (or use dataset_size)."), P("dataset_size", "integer", "Number of images.", default=40),
          P("target", "string", "Training target.", default="character"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("training-personalization.dreambooth-training-specialist", "production:dreambooth_training", "plan", "write",
         "Plan class images, prior preservation, identity tests and overfitting controls.",
         [P("subject", "string", "Subject to personalise.", True), P("class", "string", "Class noun.", default="person"),
          P("dataset_size", "integer", "Number of subject images.", default=12),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("training-personalization.textual-inversion-trainer", "production:textual_inversion", "plan", "write",
         "Plan learned embeddings with token tests and overfitting limits.",
         [P("concept", "string", "Concept to learn.", True), P("examples", "integer", "Number of training examples.", default=6),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("training-personalization.fine-tune-evaluation-designer", "production:finetune_evaluation", "artifact", "write",
         "Build a fixed prompt suite scoring identity, flexibility, leakage, anatomy and adherence.",
         [P("target", "string", "What was trained.", True), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("training-personalization.overfitting-and-memorization-auditor", "production:overfitting_auditor", "audit", "safe",
         "Detect copied poses, backgrounds and near-duplicates and recommend corrective changes.",
         [P("dataset", "path", "Dataset path."), P("caption_field", "string", "Caption column.", default="caption"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("training-personalization.model-merge-and-blend-evaluator", "production:model_merge_evaluator", "plan", "write",
         "Plan checkpoint and adapter merges with compatibility checks and a comparison suite.",
         [P("models", "string", "Comma separated models or adapters to merge."), P("ratios", "string", "Blend ratios.", default="0.5,0.5"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 17 - Data Governance, Rights, and Provenance
# ---------------------------------------------------------------------------
register("governance-provenance.training-data-rights-reviewer", "production:rights_reviewer", "audit", "safe",
         "Document dataset sources, licences, consent, retention and removal paths before training.",
         [P("dataset", "path", "Dataset manifest (optional)."), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("governance-provenance.copyright-risk-screener", "production:copyright_screener", "audit", "safe",
         "Screen prompts for close imitation, protected characters, logos and trade dress for legal review.",
         [P("prompt", "string", "Prompt or brief to screen.", True),
          P("context", "string", "Commercial context.", default="commercial"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("governance-provenance.artist-style-request-rewriter", "production:style_rewriter", "artifact", "write",
         "Rewrite requests naming living artists into high-level visual attributes.",
         [P("prompt", "string", "Prompt to rewrite.", True), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("governance-provenance.consent-and-likeness-checker", "production:consent_checker", "audit", "safe",
         "Verify authorisation and intended use for identifiable people and sensitive contexts.",
         [P("subject", "string", "Who or what is depicted.", True),
          P("records", "path", "Consent record JSON (optional)."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("governance-provenance.provenance-metadata-manager", "production:provenance_manager", "transform", "write",
         "Write and read provenance sidecars and embed them into PNG tEXt chunks.",
         [P("image", "path", "Image to tag (optional)."),
          P("sidecar", "path", "Existing sidecar JSON to read (optional)."),
          P("prompt", "string", "Prompt used."), P("model", "string", "Model identifier."), P("seed", "integer", "Seed."),
          P("embed", "boolean", "Embed the provenance into a PNG copy.", default=False),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("governance-provenance.synthetic-media-disclosure-writer", "production:disclosure_writer", "artifact", "write",
         "Write clear, context-appropriate synthetic media labels and disclosures.",
         [P("context", "string", "Disclosure context.", default="social_post", choices=["social_post", "press_release", "email", "report", "dataset"]),
          P("details", "string", "What was generated or edited."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("governance-provenance.privacy-redaction-specialist", "production:privacy_redactor", "transform", "destructive",
         "Strip embedded metadata and produce a privacy redaction plan for faces, plates and documents.",
         [P("image", "path", "Image to redact (optional)."),
          P("apply", "boolean", "Write a metadata-stripped copy.", default=False),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("governance-provenance.dataset-bias-auditor", "production:bias_auditor", "analyze", "safe",
         "Measure representation, caption quality, stereotype risk and imbalance across a caption corpus.",
         [P("dataset", "path", "JSONL or CSV caption dataset."), P("caption_field", "string", "Caption column.", default="caption"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 18 - Safety, Authenticity, and Misuse Prevention
# ---------------------------------------------------------------------------
register("safety-authenticity.deepfake-risk-assessor", "production:deepfake_assessor", "audit", "safe",
         "Score impersonation, deception and reputational risk and recommend safeguards.",
         [P("scenario", "string", "What the image would be used for.", True),
          P("subject", "string", "Is a real, identifiable person depicted?"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("safety-authenticity.image-authenticity-analyst", "media:authenticity_analyst", "analyze", "safe",
         "Inspect metadata, compression and generation artefacts and report uncertainty honestly.",
         [P("image", "path", "Image to analyse."), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("safety-authenticity.content-safety-classifier", "production:content_safety_classifier", "audit", "safe",
         "Classify requests and outputs against violence, sexual, privacy, fraud and exploitation policy axes.",
         [P("text", "string", "Request or description to classify.", True),
          P("image", "path", "Image to inspect (optional)."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("safety-authenticity.age-sensitive-image-reviewer", "production:age_sensitive_reviewer", "audit", "safe",
         "Apply strict safeguards to content involving minors or age ambiguity.",
         [P("description", "string", "What is depicted or requested.", True),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("safety-authenticity.misleading-edit-detector", "media:misleading_edit_detector", "analyze", "safe",
         "Identify edits that materially change events, evidence, documents, products or property claims.",
         [P("before", "path", "Original image."), P("after", "path", "Edited image."),
          P("claim", "string", "Claim the edit is meant to support."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("safety-authenticity.watermark-and-disclosure-planner", "creative:watermark_planner", "artifact", "write",
         "Plan durable visible labels and invisible provenance and render a visible label layer.",
         [P("image", "path", "Image to label (optional)."), P("text", "string", "Disclosure text.", default="AI-generated image"),
          P("mode", "string", "Label mode.", default="visible", choices=["visible", "metadata_only", "both"]),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("safety-authenticity.adversarial-prompt-defense-designer", "production:adversarial_prompt_defense", "analysis", "safe",
         "Detect obfuscation, encoding, homoglyph and multi-step attempts to bypass image safeguards.",
         [P("prompt", "string", "Incoming prompt to inspect.", True),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("safety-authenticity.safe-release-checklist-builder", "production:release_checklist", "artifact", "write",
         "Create pre-publication checks for rights, privacy, factuality, disclosure, brand and accessibility.",
         [P("channel", "string", "Release channel.", default="web"),
          P("media_type", "string", "Media type.", default="marketing_image"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 19 - Quality Assurance and Evaluation
# ---------------------------------------------------------------------------
register("quality-evaluation.visual-quality-reviewer", "media:visual_quality_reviewer", "audit", "safe",
         "Score composition, exposure, detail, noise, artefacts and finish with a repeatable rubric.",
         [P("image", "path", "Image to review."), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("quality-evaluation.prompt-adherence-evaluator", "media:prompt_adherence_evaluator", "audit", "safe",
         "Verify every requested subject, attribute, relationship, count and exclusion against the output.",
         [P("prompt", "string", "The request that was made.", True),
          P("observations", "string", "Observed content of the result."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("quality-evaluation.aesthetic-benchmark-designer", "production:aesthetic_benchmark", "artifact", "write",
         "Design fair blind comparisons with standardised prompts, outputs, raters and scoring.",
         [P("prompt", "string", "Standard prompt.", True), P("candidates", "integer", "Number of candidates.", default=4),
          P("raters", "integer", "Number of raters.", default=3),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("quality-evaluation.artifact-detection-specialist", "media:artifact_detector", "analyze", "safe",
         "Find banding, tiling, halos, seams, blocking, duplicated regions and broken geometry.",
         [P("image", "path", "Image to inspect."), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("quality-evaluation.image-pair-comparator", "media:image_pair_comparator", "analyze", "safe",
         "Compare revisions at pixel, structural and perceptual levels and isolate changed regions.",
         [P("before", "path", "Before image."), P("after", "path", "After image."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("quality-evaluation.regression-test-builder", "production:regression_test_builder", "artifact", "write",
         "Maintain reference prompts and expected characteristics and check outputs against them.",
         [P("suite", "path", "Regression suite JSON to create or update."),
          P("image", "path", "Image to check against the suite (optional)."),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("quality-evaluation.human-review-protocol-designer", "production:human_review_protocol", "artifact", "write",
         "Define reviewer instructions, sampling, disagreement handling, escalation and thresholds.",
         [P("workflow", "string", "Workflow being reviewed.", default="batch_generation"),
          P("volume", "integer", "Daily review volume.", default=200),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("quality-evaluation.delivery-readiness-auditor", "media:delivery_readiness_auditor", "audit", "safe",
         "Confirm dimensions, format, colour space, alpha, metadata and naming for delivery.",
         [P("image", "path", "Image to audit."), P("channel", "string", "Delivery channel.", default="web"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 20 - Automation, APIs, and Batch Pipelines
# ---------------------------------------------------------------------------
register("automation-batch.image-generation-api-integrator", "production:api_integrator", "external", "external",
         "Design and optionally exercise a bounded, retrying, idempotent image-generation API client.",
         [P("endpoint", "string", "Generation endpoint URL (optional; dry-run when omitted)."),
          P("prompt", "string", "Prompt to send.", default="a quiet harbour at dawn"),
          P("model", "string", "Model identifier.", default="sdxl"),
          P("timeout", "number", "Per-request timeout in seconds.", default=30),
          P("retries", "integer", "Retry attempts for transient failures.", default=2),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("automation-batch.batch-prompt-runner", "production:batch_runner", "artifact", "write",
         "Run a deterministic prompt sweep with parameter grids, stable naming, resume and failure isolation.",
         [P("prompts", "path", "Prompts JSON/JSONL/CSV file."), P("width", "integer", "Width.", default=1024),
          P("height", "integer", "Height.", default=1024), P("seeds", "integer", "Number of seeds per prompt.", default=2),
          P("resume", "boolean", "Skip items already completed.", default=True),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("automation-batch.comfyui-api-automator", "production:comfyui_automator", "external", "external",
         "Parameterise ComfyUI workflows, submit jobs, poll the queue and preserve workflow JSON.",
         [P("workflow", "path", "ComfyUI workflow JSON to submit."),
          P("endpoint", "string", "ComfyUI base URL (optional; dry-run when omitted)."),
          P("prompt", "string", "Prompt to inject into the CLIP node."),
          P("timeout", "number", "Overall timeout in seconds.", default=120),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("automation-batch.asset-naming-and-metadata-automator", "production:asset_naming", "transform", "write",
         "Apply stable filenames, folders, tags and metadata sidecars across an asset folder.",
         [P("path", "path", "Folder of assets."), P("campaign", "string", "Campaign identifier.", default="campaign"),
          P("template", "string", "Naming template.", default="{campaign}_{index}_{slug}"),
          P("copy", "boolean", "Copy instead of renaming in place.", default=True),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("automation-batch.gpu-queue-orchestrator", "production:gpu_queue_orchestrator", "compute", "write",
         "Schedule jobs against worker VRAM capacity with priority, load cost, timeout and retry policy.",
         [P("jobs", "path", "Job list JSON (optional; a demo set is used when omitted)."),
          P("workers", "integer", "Number of workers.", default=2), P("vram_gb", "number", "Worker VRAM in GB.", default=12),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("automation-batch.cost-and-throughput-optimizer", "production:cost_optimizer", "compute", "write",
         "Estimate images per hour and per dollar and tune batch size, steps, resolution and caching.",
         [P("model", "string", "Model family.", default="sdxl"), P("steps", "integer", "Steps per image.", default=30),
          P("batch_size", "integer", "Batch size.", default=1), P("resolution", "string", "WxH.", default="1024x1024"),
          P("hourly_cost", "number", "Cost per GPU hour in USD.", default=0.85),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("automation-batch.automated-qa-pipeline-builder", "production:qa_pipeline_builder", "artifact", "write",
         "Run dimension, corruption, blankness, similarity, safety and metadata checks over a folder.",
         [P("path", "path", "Folder of assets."), P("policy", "string", "Policy preset.", default="default"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("automation-batch.human-in-the-loop-image-workflow-designer", "production:hitl_workflow", "artifact", "write",
         "Route candidates through review, annotation, revision, approval and publishing with lineage.",
         [P("workflow", "string", "Workflow name.", default="batch_generation"), P("volume", "integer", "Daily volume.", default=500),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 21 - Performance and Hardware Optimization
# ---------------------------------------------------------------------------
register("performance-hardware.vram-budget-planner", "production:vram_planner", "compute", "safe",
         "Estimate memory for checkpoints, encoders, VAE, ControlNets, adapters, resolution and precision.",
         [P("model", "string", "Model family.", default="sdxl"),
          P("width", "integer", "Width.", default=1024), P("height", "integer", "Height.", default=1024),
          P("batch_size", "integer", "Batch size.", default=1), P("precision", "string", "Precision.", default="fp16",
            choices=["fp32", "fp16", "bf16", "fp8", "int8"]),
          P("controlnets", "integer", "ControlNet adapters loaded.", default=0),
          P("loras", "integer", "LoRA adapters loaded.", default=0),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("performance-hardware.low-memory-diffusion-optimizer", "production:low_memory_optimizer", "plan", "write",
         "Apply attention slicing, tiled VAE, offload, quantization and small batches with trade-offs.",
         [P("vram_gb", "number", "Available VRAM in GB.", default=8), P("model", "string", "Model family.", default="sdxl"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("performance-hardware.inference-speed-tuner", "production:speed_tuner", "plan", "write",
         "Benchmark compile, efficient attention, distillation, latent caching and step reduction.",
         [P("model", "string", "Model family.", default="sdxl"), P("target", "string", "Target images per hour.", default=20),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("performance-hardware.gpu-compatibility-troubleshooter", "production:gpu_troubleshooter", "diagnose", "safe",
         "Diagnose driver, CUDA, ROCm, Metal, DirectML, precision and kernel compatibility failures.",
         [P("symptom", "string", "Error text or symptom.", default=""), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("performance-hardware.model-storage-manager", "production:model_storage_manager", "analyze", "write",
         "Organise checkpoints, adapters, embeddings and VAEs with hashes, duplicates and a plan.",
         [P("path", "path", "Model directory."), P("apply", "boolean", "Write the manifest report only (default).", default=False),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("performance-hardware.environment-reproducibility-builder", "production:env_reproducibility", "artifact", "write",
         "Capture the runtime, pin dependencies and write a clean rebuild script.",
         [P("out_dir", "string", "Directory for generated artefacts.", default="output"),
          P("name", "string", "Environment name.", default="diffusion-env")])
register("performance-hardware.thermal-and-reliability-monitor", "production:thermal_monitor", "analyze", "safe",
         "Parse GPU telemetry samples and flag thermal throttling, memory pressure and crashes.",
         [P("log", "path", "Telemetry log file (nvidia-smi query output)."), P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("performance-hardware.cloud-gpu-deployment-planner", "production:cloud_gpu_planner", "plan", "write",
         "Choose instance classes, storage, startup images, security controls and cost caps for hosted diffusion.",
         [P("model", "string", "Model family.", default="sdxl"), P("concurrency", "integer", "Concurrent workers.", default=2),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 22 - Specialized Media and Asset Production
# ---------------------------------------------------------------------------
register("specialized-media.game-concept-asset-maker", "creative:game_concept_assets", "artifact", "write",
         "Create characters, environments, props, icons and paintovers aligned to an art bible.",
         [P("game", "string", "Game or project name.", True), P("asset_type", "string", "Asset type.", default="environment"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("specialized-media.texture-and-material-generator", "media:texture_generator", "artifact", "write",
         "Generate a genuinely seamless tileable texture and verify its tiling.",
         [P("size", "integer", "Texture size in pixels.", default=256), P("seed", "integer", "Generation seed.", default=11),
          P("pattern", "string", "Pattern family.", default="stone"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("specialized-media.sprite-and-pixel-art-generator", "media:sprite_generator", "artifact", "write",
         "Render grid-aligned sprite frames and an animation-ready frame plan at a target pixel scale.",
         [P("subject", "string", "Sprite subject.", True), P("scale", "integer", "Pixels per sprite cell.", default=32),
          P("frames", "integer", "Number of frames.", default=4),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("specialized-media.comic-and-storyboard-creator", "creative:comic_storyboard", "artifact", "write",
         "Plan panels, shot continuity, staging and speech-safe areas across a sequence.",
         [P("story", "string", "Story or sequence outline.", True), P("panels", "integer", "Panel count.", default=6),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("specialized-media.book-illustration-planner", "creative:book_illustration_planner", "plan", "write",
         "Map manuscript beats to page turns, recurring characters and print-safe compositions.",
         [P("manuscript", "string", "Manuscript summary.", True), P("pages", "integer", "Target page count.", default=32),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("specialized-media.thumbnail-and-keyframe-generator", "creative:thumbnail_generator", "artifact", "write",
         "Create high-contrast small-format visuals with controlled focal points and crop-safe variants.",
         [P("topic", "string", "Thumbnail topic.", True), P("ratio", "string", "Aspect ratio.", default="16:9"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("specialized-media.icon-family-designer", "creative:icon_family_designer", "artifact", "write",
         "Design a cohesive icon system with consistent stroke, corner, grid and optical weight.",
         [P("set", "string", "Icon set name.", True), P("count", "integer", "Number of icons.", default=6),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("specialized-media.pattern-and-surface-design-maker", "media:pattern_generator", "artifact", "write",
         "Generate seamless repeats, half-drops and border prints with coordinated colorways.",
         [P("pattern", "string", "Pattern family.", default="geometric"), P("size", "integer", "Tile size.", default=256),
          P("seed", "integer", "Seed.", default=23), P("out_dir", "string", "Directory for generated artefacts.", default="output")])

# ---------------------------------------------------------------------------
# Category 23 - Accessibility, Localization, and Delivery
# ---------------------------------------------------------------------------
register("accessibility-delivery.alt-text-writer-for-images", "creative:alt_text_writer", "artifact", "write",
         "Write concise and extended alternative text from structured scene details and purpose.",
         [P("details", "string", "Structured description of the image.", True),
          P("purpose", "string", "Purpose of the image in context.", default="informative"),
          P("length", "string", "Alt text length.", default="concise", choices=["concise", "extended"]),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("accessibility-delivery.color-contrast-accessibility-reviewer", "media:contrast_reviewer", "audit", "safe",
         "Check foreground/background contrast, colour-only encoding and grayscale distinguishability.",
         [P("foreground", "string", "Foreground hex colour.", True), P("background", "string", "Background hex colour.", True),
          P("size", "string", "Text size.", default="normal", choices=["normal", "large"]),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("accessibility-delivery.localization-safe-visual-designer", "creative:localization_designer", "plan", "write",
         "Reserve flexible text space and avoid culture-specific ambiguity for localisation.",
         [P("asset", "string", "Asset description.", True), P("locales", "string", "Target locales.", default="en-US,de-DE,ja-JP,ar-AE"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("accessibility-delivery.cultural-context-reviewer", "production:cultural_reviewer", "audit", "safe",
         "Flag symbols, gestures, clothing, colours and scenarios that may be inaccurate or sensitive.",
         [P("description", "string", "What the image shows or will show.", True),
          P("locale", "string", "Target locale.", default="global"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("accessibility-delivery.multi-format-export-specialist", "media:export_specialist", "transform", "write",
         "Export PNG, JPEG, WebP, TIFF and layered print-ready files with suitable compression and alpha.",
         [P("image", "path", "Source image."), P("formats", "string", "Comma separated target formats.", default="png,jpeg,webp"),
          P("quality", "integer", "JPEG/WebP quality.", default=90),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("accessibility-delivery.responsive-crop-planner", "media:responsive_crop_planner", "compute", "write",
         "Compute focal-point-aware crops for desktop, mobile, social, banner and thumbnail ratios.",
         [P("image", "path", "Source image (optional)."),
          P("focal", "string", "Focal point as 'x,y' in 0-1 coordinates.", default="0.5,0.5"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("accessibility-delivery.image-optimization-specialist", "media:image_optimizer", "transform", "write",
         "Reduce file size through dimensions, quality, chroma and metadata while meeting visual thresholds.",
         [P("image", "path", "Source image."), P("max_width", "integer", "Maximum output width.", default=2000),
          P("quality", "integer", "Encoder quality.", default=82),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])
register("accessibility-delivery.archive-and-handoff-manager", "production:handoff_manager", "artifact", "write",
         "Package editable sources, exports, prompts, licences and metadata with a handoff README.",
         [P("path", "path", "Directory to package."), P("name", "string", "Package name.", default="image-handoff"),
          P("out_dir", "string", "Directory for generated artefacts.", default="output")])


# ---------------------------------------------------------------------------
# Registry assembly
# ---------------------------------------------------------------------------

def build_registry() -> List[Dict[str, Any]]:
    """Merge the packaged catalog with the capability specs into one ordered list."""
    catalog = load_asset(CATALOG_ASSET)
    out: List[Dict[str, Any]] = []
    for cat in catalog["categories"]:
        for skill in cat["skills"]:
            spec = SPECS.get(skill["id"])
            if spec is None:
                raise KeyError(f"no spec registered for {skill['id']}")
            out.append({
                "id": skill["id"],
                "category_number": cat["number"],
                "category": cat["name"],
                "category_slug": cat["slug"],
                "skill_name": skill["skill_name"],
                "description": skill["description"],
                "kind": spec["kind"],
                "risk": spec["risk"],
                "summary": spec["summary"],
                "params": spec["params"],
                "handler": spec["handler"],
            })
    return out


def get_capability(cap_id: str) -> Dict[str, Any]:
    for cap in build_registry():
        if cap["id"] == cap_id:
            return cap
    raise KeyError(cap_id)


def category_index() -> List[Dict[str, Any]]:
    catalog = load_asset(CATALOG_ASSET)
    out = []
    for cat in catalog["categories"]:
        caps = [c for c in build_registry() if c["category_slug"] == cat["slug"]]
        out.append({
            "number": cat["number"],
            "name": cat["name"],
            "slug": cat["slug"],
            "count": len(caps),
            "capabilities": [c["id"] for c in caps],
        })
    return out
