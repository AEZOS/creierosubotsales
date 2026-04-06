import logging
from aiogram.types import Message, InlineKeyboardMarkup, CallbackQuery

async def smart_edit(event: Message | CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup = None, **kwargs):
    """
    Safely edits a message to show text and a keyboard. 
    Handles cases where the message is a photo/media by deleting and resending.
    """
    if isinstance(event, CallbackQuery):
        message = event.message
    else:
        message = event

    if not message:
        return

    # If it's a photo/media message, we can't edit_text. 
    # We delete and resend to ensure the UI looks consistent and 'clean'.
    if message.photo or message.animation or message.video or message.document:
        try:
            await message.delete()
        except Exception as e:
            logging.debug(f"SmartEdit: Failed to delete media message: {e}")
        
        return await message.answer(text, reply_markup=reply_markup, **kwargs)
    
    # If it's a regular text message, we try to edit it.
    try:
        return await message.edit_text(text, reply_markup=reply_markup, **kwargs)
    except Exception as e:
        # If text is same, just ignore.
        if "message is not modified" in str(e):
            return message
        
        # Fallback: delete and send new if edit failed for some reason (e.g. message too old)
        try:
            await message.delete()
        except:
            pass
        return await message.answer(text, reply_markup=reply_markup, **kwargs)
