import os
import sys
from PIL import Image

def process_icons(source_path, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    
    img = Image.open(source_path)
    
    # 1. Standard 192x192
    img_192 = img.resize((192, 192), Image.Resampling.LANCZOS)
    img_192.save(os.path.join(output_dir, "icon-192x192.png"), "PNG")
    print("Saved icon-192x192.png")
    
    # 2. Standard 512x512
    img_512 = img.resize((512, 512), Image.Resampling.LANCZOS)
    img_512.save(os.path.join(output_dir, "icon-512x512.png"), "PNG")
    print("Saved icon-512x512.png")
    
    # 3. Maskable icon 512x512 (with padding)
    # We resize the logo to 360x360 (approx 70% of 512) and place it on a solid background
    # Let's extract the top-left pixel color as the background, or default to dark indigo/purple
    bg_color = img.getpixel((10, 10))
    if len(bg_color) == 4 and bg_color[3] == 0:
        # If transparent, use purple
        bg_color = (147, 51, 234, 255) # #9333ea
        
    maskable = Image.new("RGBA", (512, 512), bg_color)
    img_inner = img.resize((360, 360), Image.Resampling.LANCZOS)
    
    # Paste centered
    offset = ((512 - 360) // 2, (512 - 360) // 2)
    maskable.paste(img_inner, offset, img_inner if img_inner.mode == 'RGBA' else None)
    
    # Convert to RGB to ensure compatibility or keep as RGBA
    maskable.save(os.path.join(output_dir, "maskable-icon-512x512.png"), "PNG")
    print("Saved maskable-icon-512x512.png")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python generate_pwa_icons.py <source_image_path> <output_dir>")
        sys.exit(1)
    process_icons(sys.argv[1], sys.argv[2])
