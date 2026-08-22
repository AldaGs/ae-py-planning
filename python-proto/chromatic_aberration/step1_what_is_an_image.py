"""
Step 1 - What a digital image really is.

Goal: build the mental model that an image is an (H, W, 3) array of numbers,
and that each channel is an independent grayscale image you can manipulate alone.
That independence is the entire basis of chromatic aberration.

Run:  python step1_what_is_an_image.py
Then open the PNG files it writes and look at them.
"""

import numpy as np
import cv2


def make_test_image(size=400):
    """Create a test image with sharp, high-contrast edges.

    Sharp edges are where chromatic aberration is most visible in real photos
    (think tree branches against a bright sky), so they make the best test bed.
    """
    # Start with a white canvas. dtype=uint8 -> values 0..255.
    # Shape is (height, width, 3). The 3 is the channel axis: B, G, R in OpenCV.
    img = np.full((size, size, 3), 255, dtype=np.uint8)

    # Draw a few solid black shapes -> maximal contrast against white.
    cv2.circle(img, (size // 2, size // 2), size // 4, (255, 0, 0), thickness=-1)
    cv2.rectangle(img, (40, 40), (140, 140), (0, 255, 0), thickness=-1)
    # A pure-color square so you can SEE the channels differ.
    cv2.rectangle(img, (size - 150, size - 150), (size - 40, size - 40),
                  (0, 0, 255), thickness=-1)  # (B=0, G=0, R=255) -> red in BGR
    return img


def main():
    img = make_test_image()

    # --- The single most important fact, printed out ---
    print("type:        ", type(img))
    print("shape:       ", img.shape, " -> (height, width, channels)")
    print("dtype:       ", img.dtype, " -> each value is an integer 0..255")
    print("one pixel at (200,200):", img[200, 200], " <- [Blue, Green, Red]")
    print("one pixel at (350,350):", img[350, 350], " <- the red square")

    # --- Split into channels. Each is a 2D (H, W) grayscale array. ---
    b, g, r = cv2.split(img)
    print("\nblue channel shape: ", b.shape, "(no channel axis - it's 2D)")

    # Save everything so you can look at it.
    cv2.imwrite("out_original.png", img)
    # To view a single channel "as itself", put it in the right slot and zero the rest.
    zero = np.zeros_like(b)
    cv2.imwrite("out_channel_red.png",   cv2.merge([zero, zero, r]))
    cv2.imwrite("out_channel_green.png", cv2.merge([zero, g, zero]))
    cv2.imwrite("out_channel_blue.png",  cv2.merge([b, zero, zero]))
    # And as plain grayscale (how much of that channel, ignoring color):
    cv2.imwrite("out_channel_red_gray.png", r)

    print("\nWrote: out_original.png, out_channel_{red,green,blue}.png, "
          "out_channel_red_gray.png")
    print("Open them and confirm: the red square is FULL in the red channel, "
          "EMPTY in blue/green.")


if __name__ == "__main__":
    main()
