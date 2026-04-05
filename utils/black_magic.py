import io
from PIL import Image, ImageDraw, ImageFont
import random

def generate_black_magic_image(text_code="ACCESS_REVOKED"):
    # Create a 512x512 black image
    img = Image.new('RGB', (600, 400), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Draw some "code" or "matrix" feel
    for i in range(20):
        x = random.randint(0, 600)
        y = random.randint(0, 400)
        draw.text((x, y), str(random.randint(0, 1)), fill=(0, 255, 0))

    # Center text
    try:
        # Try to find a font, but fallback to default
        font = ImageFont.load_default()
    except:
        font = None
        
    draw.text((200, 180), f" [ {text_code} ] ", fill=(255, 255, 255), font=font)
    draw.text((150, 210), "STEALTH MODE ACTIVE: SYSTEM ENCRYPTED", fill=(255, 0, 0), font=font)

    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    buf.seek(0)
    return buf

def apply_pink_overlay(image_path_or_buf):
    """
    Applies a pink/magenta aesthetic overlay to an image.
    This creates a cooler, updated look as requested.
    """
    try:
        from PIL import Image
        if isinstance(image_path_or_buf, str):
            img = Image.open(image_path_or_buf).convert('RGB')
        else:
            img = Image.open(image_path_or_buf).convert('RGB')
            
        overlay = Image.new('RGB', img.size, color=(255, 20, 147)) # Deep Pink
        # Use simple alpha blending for cool tint
        img = Image.blend(img, Image.new('RGB', img.size, color=(255, 105, 180)), alpha=0.1) 
        
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=95)
        buf.seek(0)
        return buf
    except Exception as e:
        return image_path_or_buf
