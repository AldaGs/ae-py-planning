# Image Processing — A Learning Syllabus

A progression from fundamentals to modern methods, aimed at someone who wants to
implement, not just read. Each module lists the core ideas and a small project to
make it concrete. Python (NumPy + OpenCV + scikit-image) is the recommended
workbench; the concepts transfer to GLSL/shaders and native plugin code.

---

## Module 0 — Foundations & Setup

**Ideas**
- What a digital image *is*: a 2D (or 3D) array of numbers; pixels, channels, bit depth
- Color spaces: RGB, grayscale, HSV, Lab, YCbCr — and *why* each exists
- Coordinate systems, image origin, row-major storage
- Tooling: NumPy arrays, reading/writing images, displaying them

**Project:** Load an image, split and view its channels, convert RGB → grayscale
three different ways (average, luminosity, perceptual) and compare.

---

## Module 1 — Point Operations (per-pixel)

**Ideas**
- Brightness, contrast, gamma correction
- Histograms: what they tell you
- Histogram equalization and stretching
- Thresholding (global, Otsu's method)
- Look-up tables (LUTs)

**Project:** Build an interactive brightness/contrast/gamma tool and an
auto-levels function driven by the histogram.

---

## Module 2 — Spatial Filtering (neighborhood operations)

**Ideas**
- Convolution and correlation — *the* central operation
- Kernels: box blur, Gaussian blur
- Sharpening (unsharp mask), embossing
- Edge detection: Sobel, Prewitt, Laplacian, then Canny
- Nonlinear filters: median (great for noise), bilateral (edge-preserving)

**Project:** Implement convolution from scratch with NumPy, then reproduce a
Gaussian blur and a Sobel edge map. Compare your version to OpenCV's.

---

## Module 3 — The Frequency Domain

**Ideas**
- The Fourier Transform intuition: images as sums of waves
- The 2D FFT; magnitude and phase spectra
- Low-pass / high-pass / band-pass filtering in frequency space
- Why blurring in space = attenuating high frequencies
- Aliasing and the sampling theorem

**Project:** Take an image to the frequency domain, remove periodic noise by
masking spikes in the spectrum, transform back.

---

## Module 4 — Geometric Transforms & Resampling

**Ideas**
- Translation, rotation, scaling, affine and perspective (homography) transforms
- Interpolation: nearest, bilinear, bicubic — and their tradeoffs
- Image warping and remapping
- Image pyramids (Gaussian and Laplacian)

**Project:** Write a function that warps one image onto a quadrilateral region of
another (the basis of "paste this poster onto that wall").

---

## Module 5 — Morphology & Binary Images

**Ideas**
- Erosion, dilation, opening, closing
- Structuring elements
- Connected components and labeling
- Distance transforms, skeletonization

**Project:** Clean up a noisy scanned/thresholded document and count distinct
objects (coins, cells, characters).

---

## Module 6 — Segmentation

**Ideas**
- Thresholding revisited, region growing
- Watershed
- Graph-based and clustering approaches (k-means on color, SLIC superpixels)
- Active contours (snakes) — conceptual

**Project:** Segment the foreground object from a photo and composite it onto a
new background.

---

## Module 7 — Features & Description

**Ideas**
- Corners: Harris
- Keypoints and descriptors: SIFT/ORB (scale & rotation invariance)
- Feature matching
- Applications: panorama stitching, image alignment

**Project:** Stitch two overlapping photos into a panorama using feature matching
and a homography.

---

## Module 8 — Compression & Representation

**Ideas**
- Redundancy and entropy
- JPEG pipeline end-to-end (DCT, quantization, entropy coding) — ties Module 3 together
- Wavelets, briefly
- Lossy vs lossless tradeoffs

**Project:** Implement a toy JPEG-style compressor: block DCT, quantize, measure
quality vs file size.

---

## Module 9 — The Modern / Learned Approach

**Ideas**
- Why CNNs are "learned convolution" — you'll already understand the kernel part
- Classic tasks reframed: denoising, super-resolution, segmentation, in-painting
- Pretrained models and how to use them
- Where classical methods still win (speed, interpretability, no data needed)

**Project:** Run a pretrained super-resolution or denoising model, then compare
its output against your classical Module 2 results on the same image.

---

## Recommended Resources

- **Gonzalez & Woods, *Digital Image Processing*** — the standard textbook; heavy
  on Modules 1–8, excellent reference.
- **Szeliski, *Computer Vision: Algorithms and Applications*** — free PDF online;
  bridges into Modules 7 and 9.
- **scikit-image and OpenCV documentation** — both have tutorial galleries that
  map almost one-to-one onto these modules.
- **The 3Blue1Brown Fourier video** — for Module 3 intuition.

---

## Suggested Pace

One module per week is comfortable if you do the project each time. Modules 0–4
are the true core — if you only did those, you'd understand most of what any
image filter or effect is doing under the hood.
