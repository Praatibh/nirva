import discord
from discord.ext import commands
from huggingface_hub import InferenceClient
import asyncio
import io
import sqlite3
import json
import time
import logging
import random
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from discord import app_commands
import os
import webserver

# ==================== CONFIGURATION ====================
TOKEN = os.environ['discordkey']  # Your Discord bot token
HF_TOKEN = os.getenv('HF_TOKEN')  # Your Hugging Face token

# Bot configuration
MAX_IMAGES_PER_USER_FREE = 10
MAX_IMAGES_PER_USER_PREMIUM = 50
COOLDOWN_SECONDS = 30
MAX_PROMPT_LENGTH = 500
DATABASE_PATH = 'bot_data.db'

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== BOT SETUP ====================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

hf_client = InferenceClient(token=HF_TOKEN)

# ==================== MODELS AND PRESETS ====================
AVAILABLE_MODELS = {
    "FLUX.1": {
        "id": "black-forest-labs/FLUX.1-schnell",
        "description": "Fast, high-quality model by Black Forest Labs",
        "speed": "Fast",
        "quality": "High"
    },
    "FLUX.1-DEV": {
        "id": "black-forest-labs/FLUX.1-dev",
        "description": "Highest quality FLUX model (slower)",
        "speed": "Slow",
        "quality": "Highest"
    },
    "SDXL": {
        "id": "stabilityai/stable-diffusion-xl-base-1.0",
        "description": "Photorealistic, high-detail Stable Diffusion XL",
        "speed": "Medium",
        "quality": "High"
    },
    "SD1.5": {
        "id": "runwayml/stable-diffusion-v1-5",
        "description": "Classic Stable Diffusion 1.5 model",
        "speed": "Fast",
        "quality": "Medium"
    }
}

STYLE_PRESETS = {
    "Photorealistic": "hyperrealistic, photographic, detailed, 8k resolution",
    "Anime": "anime style, manga, cel shading, vibrant colors",
    "Oil Painting": "oil painting, classical art, brush strokes, artistic",
    "Cyberpunk": "cyberpunk, neon lights, futuristic, dark atmosphere",
    "Fantasy": "fantasy art, magical, ethereal, mystical",
    "Minimalist": "minimalist, clean, simple, modern design",
    "Watercolor": "watercolor painting, soft colors, flowing",
    "Digital Art": "digital art, concept art, detailed illustration"
}

QUALITY_PRESETS = {
    "Draft": {"steps": 20, "guidance": 7.5},
    "Standard": {"steps": 30, "guidance": 7.5},
    "High": {"steps": 50, "guidance": 7.5},
    "Ultra": {"steps": 80, "guidance": 7.5}
}

# ==================== DATABASE SETUP ====================
def init_database():
    """Initialize SQLite database with all required tables"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            total_generations INTEGER DEFAULT 0,
            daily_generations INTEGER DEFAULT 0,
            last_generation_date DATE,
            preferred_model TEXT DEFAULT 'FLUX.1',
            preferred_style TEXT DEFAULT 'Photorealistic',
            preferred_quality TEXT DEFAULT 'Standard',
            is_premium BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Generation history table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS generations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            prompt TEXT,
            model_used TEXT,
            style_used TEXT,
            quality_used TEXT,
            generation_time REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    """)
    
    conn.commit()
    conn.close()

# ==================== DATABASE MANAGER ====================
class DatabaseManager:
    @staticmethod
    def get_user_data(user_id: str) -> Dict:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        today = datetime.now().date()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            cursor.execute("""
                INSERT INTO users (user_id, last_generation_date)
                VALUES (?, ?)
            """, (user_id, today))
            conn.commit()
            user = (user_id, "", 0, 0, str(today), "FLUX.1", "Photorealistic", "Standard", False, datetime.now())
        
        # Reset daily count if new day
        if user[4] != str(today):
            cursor.execute("""
                UPDATE users SET daily_generations = 0, last_generation_date = ?
                WHERE user_id = ?
            """, (today, user_id))
            conn.commit()
            user = list(user)
            user[3] = 0
            user[4] = str(today)
        
        conn.close()
        return {
            'user_id': user[0],
            'username': user[1],
            'total_generations': user[2],
            'daily_generations': user[3],
            'last_generation_date': user[4],
            'preferred_model': user[5],
            'preferred_style': user[6],
            'preferred_quality': user[7],
            'is_premium': bool(user[8])
        }
    
    @staticmethod
    def update_user_generation(user_id: str, prompt: str, model: str, style: str, quality: str, generation_time: float):
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE users SET 
                total_generations = total_generations + 1,
                daily_generations = daily_generations + 1
            WHERE user_id = ?
        """, (user_id,))
        
        cursor.execute("""
            INSERT INTO generations (user_id, prompt, model_used, style_used, quality_used, generation_time)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, prompt, model, style, quality, generation_time))
        
        conn.commit()
        conn.close()
    
    @staticmethod
    def update_user_preferences(user_id: str, model: str = None, style: str = None, quality: str = None):
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        updates = []
        values = []
        
        if model:
            updates.append("preferred_model = ?")
            values.append(model)
        if style:
            updates.append("preferred_style = ?")
            values.append(style)
        if quality:
            updates.append("preferred_quality = ?")
            values.append(quality)
        
        if updates:
            values.append(user_id)
            cursor.execute(f"""
                UPDATE users SET {', '.join(updates)}
                WHERE user_id = ?
            """, values)
            conn.commit()
        
        conn.close()

# ==================== ADVANCED IMAGE VIEW ====================
class AdvancedImageView(discord.ui.View):
    def __init__(self, image_url: str, prompt: str, user_id: str, model_name: str, style: str, quality: str):
        super().__init__(timeout=300)
        self.image_url = image_url
        self.prompt = prompt
        self.user_id = user_id
        self.model_name = model_name
        self.style = style
        self.quality = quality
        self.zoom_level = 1
        self.last_interaction = time.time()
    
    async def check_cooldown(self, interaction: discord.Interaction) -> bool:
        if time.time() - self.last_interaction < 5:
            await interaction.response.send_message("Please wait a moment before using another button.", ephemeral=True)
            return False
        self.last_interaction = time.time()
        return True
    
    @discord.ui.button(label="🎲 Variation", style=discord.ButtonStyle.secondary)
    async def variation_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_cooldown(interaction):
            return
            
        await interaction.response.defer(ephemeral=True)
        try:
            variation_modifiers = ["artistic variation", "different perspective", "alternative style", "creative interpretation"]
            modifier = random.choice(variation_modifiers)
            variation_prompt = f"{self.prompt}, {modifier}"
            
            start_time = time.time()
            image = hf_client.text_to_image(
                variation_prompt,
                model=AVAILABLE_MODELS[self.model_name]["id"]
            )
            generation_time = time.time() - start_time
            
            img_bytes = io.BytesIO()
            image.save(img_bytes, format='PNG')
            img_bytes.seek(0)
            file = discord.File(img_bytes, filename='variation.png')
            
            embed = discord.Embed(
                title="🎲 Variation Generated",
                description=f"**Original:** {self.prompt}\n**Variation:** {modifier}\n**Model:** {self.model_name}\n**Time:** {generation_time:.2f}s",
                color=0x0099ff
            )
            embed.set_image(url="attachment://variation.png")
            embed.set_footer(text=f"Requested by {interaction.user.display_name}")
            
            new_view = AdvancedImageView("attachment://variation.png", variation_prompt, self.user_id, self.model_name, self.style, self.quality)
            await interaction.followup.send(file=file, embed=embed, view=new_view, ephemeral=True)
            
            DatabaseManager.update_user_generation(self.user_id, variation_prompt, self.model_name, self.style, self.quality, generation_time)
            
        except Exception as e:
            logger.error(f"Variation failed for user {self.user_id}: {e}")
            await interaction.followup.send(f"❌ Variation failed: {str(e)}", ephemeral=True)
    
    @discord.ui.button(label="🔍 Zoom In", style=discord.ButtonStyle.primary)
    async def zoom_in_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_cooldown(interaction):
            return
        await self.generate_zoomed_image(interaction, zoom_in=True)
    
    @discord.ui.button(label="🔎 Zoom Out", style=discord.ButtonStyle.primary)
    async def zoom_out_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_cooldown(interaction):
            return
        await self.generate_zoomed_image(interaction, zoom_in=False)
    
    @discord.ui.button(label="📤 Send to DM", style=discord.ButtonStyle.success)
    async def send_dm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            embed = discord.Embed(
                title="Your Generated Image",
                description=f"**Prompt:** {self.prompt}\n**Model:** {self.model_name}\n**Style:** {self.style}",
                color=0x00ff00
            )
            
            await interaction.user.send(embed=embed)
            await interaction.user.send(f"Generated with: {self.prompt}")
            await interaction.response.send_message("Sent to your DMs!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Unable to send DM. Please check your privacy settings.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message("Failed to send DM.", ephemeral=True)
    
    async def generate_zoomed_image(self, interaction: discord.Interaction, zoom_in: bool = True):
        """Generate a zoomed version of the image - FIXED VERSION"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Generate zoom modifiers
            if zoom_in:
                zoom_modifiers = ["extreme close-up", "macro shot", "detailed close-up", "zoomed in view"]
                new_zoom_level = self.zoom_level + 1
            else:
                zoom_modifiers = ["wide shot", "distant view", "pulled back", "zoomed out perspective"]
                new_zoom_level = max(1, self.zoom_level - 1)
            
            modifier = random.choice(zoom_modifiers)
            zoom_prompt = f"{self.prompt}, {modifier}, {STYLE_PRESETS[self.style]}"
            
            start_time = time.time()
            image = hf_client.text_to_image(
                zoom_prompt,
                model=AVAILABLE_MODELS[self.model_name]["id"]
            )
            generation_time = time.time() - start_time
            
            # Process image - PROPERLY DEFINED
            img_bytes = io.BytesIO()
            image.save(img_bytes, format='PNG')
            img_bytes.seek(0)
            file = discord.File(img_bytes, filename='zoomed_image.png')
            
            # Create embed - PROPERLY DEFINED
            zoom_direction = "🔍 Zoomed In" if zoom_in else "🔎 Zoomed Out"
            embed = discord.Embed(
                title=f"{zoom_direction} (Level {new_zoom_level})",
                description=f"**Prompt:** {zoom_prompt}\n**Model:** {self.model_name}\n**Time:** {generation_time:.2f}s",
                color=0xffaa00
            )
            embed.set_image(url="attachment://zoomed_image.png")
            embed.set_footer(text=f"Requested by {interaction.user.display_name}")
            
            # Create new view with updated zoom level - PROPERLY DEFINED
            new_view = AdvancedImageView("attachment://zoomed_image.png", zoom_prompt, self.user_id, self.model_name, self.style, self.quality)
            new_view.zoom_level = new_zoom_level
            
            await interaction.followup.send(file=file, embed=embed, view=new_view, ephemeral=True)
            
            # Update database
            DatabaseManager.update_user_generation(self.user_id, zoom_prompt, self.model_name, self.style, self.quality, generation_time)
            
        except Exception as e:
            logger.error(f"Zoom failed for user {self.user_id}: {e}")
            await interaction.followup.send(f"❌ Zoom failed: {str(e)}", ephemeral=True)

# ==================== BOT EVENTS ====================
@bot.event
async def on_ready():
    logger.info(f"🚀 {bot.user} is now online!")
    print(f"🚀 Bot ready as {bot.user} (ID: {bot.user.id})")
    
    init_database()
    
    try:
        synced = await bot.tree.sync()
        logger.info(f"✅ Synced {len(synced)} global commands")
        print(f"✅ Synced {len(synced)} global commands")
        
        for command in synced:
            print(f"   - /{command.name}: {command.description}")
            
    except Exception as e:
        logger.error(f"❌ Command sync failed: {e}")
        print(f"❌ Sync failed: {e}")

# ==================== AUTOCOMPLETE FUNCTIONS ====================
async def models_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    return [
        app_commands.Choice(
            name=f"{name} - {data['description']}", 
            value=name
        )
        for name, data in AVAILABLE_MODELS.items()
        if current.lower() in name.lower() or current.lower() in data['description'].lower()
    ]

async def styles_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=style, value=style)
        for style in STYLE_PRESETS.keys()
        if current.lower() in style.lower()
    ]

async def quality_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=f"{quality} ({data['steps']} steps)", value=quality)
        for quality, data in QUALITY_PRESETS.items()
        if current.lower() in quality.lower()
    ]

# ==================== UTILITY FUNCTIONS ====================
def validate_prompt(prompt: str) -> tuple[bool, str]:
    if len(prompt) > MAX_PROMPT_LENGTH:
        return False, f"Prompt too long! Maximum {MAX_PROMPT_LENGTH} characters."
    
    blocked_words = ["nsfw", "explicit"]
    if any(word in prompt.lower() for word in blocked_words):
        return False, "Prompt contains blocked content."
    
    return True, ""

def enhance_prompt(prompt: str, style: str, quality: str) -> str:
    enhanced = prompt
    
    if style in STYLE_PRESETS:
        enhanced += f", {STYLE_PRESETS[style]}"
    
    if quality in ["High", "Ultra"]:
        enhanced += ", high quality, detailed, sharp"
    
    return enhanced

# ==================== MAIN COMMANDS ====================
@bot.tree.command(name="imagine", description="Generate an AI image from your prompt")
@app_commands.describe(
    prompt="Describe the image you want to generate",
    style="Choose an art style (optional)",
    quality="Choose generation quality (optional)",
    model="Choose AI model (optional)"
)
@app_commands.autocomplete(
    style=styles_autocomplete,
    quality=quality_autocomplete,
    model=models_autocomplete
)
async def imagine(
    interaction: discord.Interaction, 
    prompt: str,
    style: Optional[str] = None,
    quality: Optional[str] = None,
    model: Optional[str] = None
):
    user_id = str(interaction.user.id)
    user_data = DatabaseManager.get_user_data(user_id)
    
    model_name = model or user_data['preferred_model']
    style_name = style or user_data['preferred_style']
    quality_name = quality or user_data['preferred_quality']
    
    max_images = MAX_IMAGES_PER_USER_PREMIUM if user_data['is_premium'] else MAX_IMAGES_PER_USER_FREE
    if user_data['daily_generations'] >= max_images:
        embed = discord.Embed(
            title="❌ Daily Limit Reached",
            description=f"You've used your daily limit of {max_images} images.\n{'Consider upgrading to premium for more generations!' if not user_data['is_premium'] else 'Premium limit reached for today.'}",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    is_valid, error_msg = validate_prompt(prompt)
    if not is_valid:
        await interaction.response.send_message(f"❌ {error_msg}", ephemeral=True)
        return
    
    enhanced_prompt = enhance_prompt(prompt, style_name, quality_name)
    
    await interaction.response.send_message(
        f"Generating your image...\n**Model:** {model_name}\n**Style:** {style_name}\n**Quality:** {quality_name}"
    )
    
    try:
        start_time = time.time()
        
        image = hf_client.text_to_image(
            enhanced_prompt,
            model=AVAILABLE_MODELS[model_name]["id"]
        )
        
        generation_time = time.time() - start_time
        
        img_bytes = io.BytesIO()
        image.save(img_bytes, format="PNG")
        img_bytes.seek(0)
        
        embed = discord.Embed(
            title="Image Generated Successfully",
            description=f"**/imagine** {prompt} **--{model_name} --{style_name}** by {interaction.user.mention}",
            color=0x00ff00
        )
        embed.add_field(name="⚡ Generation Time", value=f"{generation_time:.2f}s", inline=True)
        embed.add_field(name="🎯 Quality", value=quality_name, inline=True)
        embed.add_field(name="📊 Usage", value=f"{user_data['daily_generations'] + 1}/{max_images}", inline=True)
        embed.set_image(url="attachment://generated_image.png")
        embed.set_footer(
            text=f"Total generations: {user_data['total_generations'] + 1}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None
        )
        
        file = discord.File(img_bytes, filename="generated_image.png")
        view = AdvancedImageView(
            "attachment://generated_image.png", 
            enhanced_prompt, 
            user_id, 
            model_name,
            style_name,
            quality_name
        )
        
        await interaction.followup.send(embed=embed, file=file, view=view)
        
        DatabaseManager.update_user_generation(user_id, enhanced_prompt, model_name, style_name, quality_name, generation_time)
        
        logger.info(f"Image generated for user {user_id}: {prompt}")
        
    except Exception as e:
        logger.error(f"Generation failed for user {user_id}: {e}")
        await interaction.followup.send(f"❌ Generation failed: {str(e)}", ephemeral=True)

@bot.tree.command(name="model", description="Choose your preferred AI model")
@app_commands.describe(model="Select an available model")
@app_commands.autocomplete(model=models_autocomplete)
async def set_model(interaction: discord.Interaction, model: str):
    user_id = str(interaction.user.id)
    
    if model not in AVAILABLE_MODELS:
        embed = discord.Embed(
            title="Invalid Model",
            description=f"Model `{model}` is not available.",
            color=0xff0000
        )
        
        for name, data in AVAILABLE_MODELS.items():
            embed.add_field(
                name=f"{name}",
                value=f"{data['description']}\n**Speed:** {data['speed']} | **Quality:** {data['quality']}",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    DatabaseManager.update_user_preferences(user_id, model=model)
    
    model_info = AVAILABLE_MODELS[model]
    embed = discord.Embed(
        title="Model Updated",
        description=f"Your preferred model has been set to **{model}**",
        color=0x00ff00
    )
    embed.add_field(name="Description", value=model_info['description'], inline=False)
    embed.add_field(name="Speed", value=model_info['speed'], inline=True)
    embed.add_field(name="Quality", value=model_info['quality'], inline=True)
    
    # Show all available models
    models_str = ', '.join(f'`{m}`' for m in AVAILABLE_MODELS.keys())
    embed.add_field(name="Available Models", value=models_str, inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)
    logger.info(f"User {user_id} changed model to {model}")

@bot.tree.command(name="models", description="List all available AI models")
async def models_list(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Available AI Models",
        description="Choose from these high-quality AI image generation models:",
        color=0x00ffcc
    )
    
    for name, data in AVAILABLE_MODELS.items():
        embed.add_field(
            name=f"{name}",
            value=f"{data['description']}\n**Speed:** {data['speed']} | **Quality:** {data['quality']}",
            inline=False
        )
    
    embed.set_footer(text="Use /model <name> to set your preferred model")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="help", description="❓ Get help with bot commands")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Nirva AI Bot - Help Guide",
        description="Your advanced AI image generation companion!",
        color=0x7289da
    )
    
    embed.add_field(
        name="**Image Generation**",
        value=(
            "`/imagine <prompt>` - Generate an AI image\n"
            "`/imagine <prompt> style:<style>` - Generate with specific style\n"
            "`/imagine <prompt> quality:<quality>` - Set generation quality\n"
            "`/imagine <prompt> model:<model>` - Use specific AI model"
        ),
        inline=False
    )
    
    embed.add_field(
        name="**Settings**",
        value=(
            "`/model <name>` - Set your preferred AI model\n"
            "`/models` - View all available models"
        ),
        inline=False
    )
    
    embed.add_field(
        name="**Interactive Features**",
        value=(
            "**Variation** - Generate artistic variations\n"
            "**Zoom In/Out** - Create zoomed perspectives\n"
            "**Send to DM** - Get images privately"
        ),
        inline=False
    )
    
    embed.add_field(
        name="**Pro Tips**",
        value=(
            "• Be descriptive in your prompts\n"
            "• Try different styles for variety\n"
            "• Use the buttons for quick variations"
        ),
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

if __name__ == "__main__":
    if not TOKEN or not HF_TOKEN:
        print("Please set your TOKEN and HF_TOKEN!")
        exit(1)
    
    try:
        bot.run(TOKEN)
    except Exception as e:
        logger.error(f"Bot failed to start: {e}")
        print(f"Bot failed to start: {e}")
