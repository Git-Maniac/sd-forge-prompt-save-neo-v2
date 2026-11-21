# ----------------------------------------------------------------------------------------------
# SD Forge Prompt Save NEO v2
# Entension for SD WebUI Forge-NEO v2.x
# ----------------------------------------------------------------------------------------------

import os
import html
import json
import time
import gradio as gr
from PIL import Image

from modules import images, infotext_utils, script_callbacks, scripts, shared, ui_extra_networks, util
from modules.ui_extra_networks import quote_js
from modules.ui_extra_networks_user_metadata import UserMetadataEditor

# ----------------------------------------------------------------------------------------------
# Settings and the prompt folder
# ----------------------------------------------------------------------------------------------

SETTING_KEY = "save_prompt_dir"

# scripts.basedir() is the extension folder, but only while this file is being imported:
# so we store it right here in a variable
EXTENSION_DIR = os.path.abspath(scripts.basedir())
DEFAULT_PROMPTS_DIR = os.path.join(EXTENSION_DIR, "saved_prompts")

# The last error message about the prompt folder ("" = no error).
# It is a dictionary, so that functions can change it without special keywords.
dir_error = {"text": ""}


def get_prompts_dir():
    # Returns the prompt folder from the WebUI setting and creates it if needed.
    # A relative value (also with a leading \) is relative to the WebUI folder.
    # If the folder cannot be used, the function returns None and the reason is in dir_error["text"].
    value = str(shared.opts.data.get(SETTING_KEY) or "").strip()
    if value == "":
        value = DEFAULT_PROMPTS_DIR  # empty setting: use the default folder

    # An absolute path (or a path with a drive letter) is used as it is, everything else is relative to the WebUI folder
    if os.path.isabs(value) or os.path.splitdrive(value)[0] != "":
        path = value
    else:
        path = os.path.join(shared.data_path, value.lstrip("\\/"))
    path = os.path.normpath(path)

    try:
        os.makedirs(path, exist_ok=True)
    except (OSError, ValueError) as e:
        message = f"Cannot create or open the prompt folder '{path}': {e}. Please check the setting 'Prompt dir' under Settings > Save prompt."
        if message != dir_error["text"]:
            print(f"[Prompt Save] {message}")  # printed only once, not at every refresh
        dir_error["text"] = message
        return None

    dir_error["text"] = ""
    return path


def get_relative_prompts_dir(card_file=None):
    # Returns a folder for display, relative to the parent of the WebUI folder,
    # e.g. \sd-webui-forge-neo\models\saved_prompts
    # Without card_file it is the prompt folder, with card_file it is the folder of that file (with subfolders).
    path = get_prompts_dir()
    if path is None:
        return "(prompt folder not available, see Settings > Save prompt > Folder path)"

    if card_file:
        path = os.path.dirname(os.path.abspath(card_file))

    try:
        webui_parent = os.path.dirname(os.path.abspath(shared.data_path))
        return os.sep + os.path.relpath(path, webui_parent)
    except ValueError:  # the folder is on a different drive: a relative path is impossible
        return path


def on_ui_settings():
    # Adds the setting 'Prompt dir' to Settings > Save prompt.
    section = ("save_prompt", "Save prompt")
    option = shared.OptionInfo(DEFAULT_PROMPTS_DIR, "Prompt dir", section=section)
    option.info("relative to the SD-WebUI root folder, or an absolute path. Default is the 'saved_prompts' folder in the extension folder")
    shared.opts.add_option(SETTING_KEY, option)


script_callbacks.on_ui_settings(on_ui_settings)


def open_prompts_folder():
    # Opens the prompt folder in the file manager (used by the folder button).
    if shared.cmd_opts.hide_ui_dir_config:
        return
    path = get_prompts_dir()
    if path is None:
        gr.Warning(dir_error["text"])
        return
    util.open_folder(path)


# ----------------------------------------------------------------------------------------------
# CSS for the hover overlay, the folder button and the edit dialog
# ----------------------------------------------------------------------------------------------

OVERLAY_CSS = """<style>
.prompt-save-overlay { display: none; position: absolute; top: 0; left: 0; right: 0; bottom: 0; z-index: 0; overflow: auto; padding: 0.5em; font-size: 0.85em; white-space: pre-wrap; color: #fff; background: rgba(0, 0, 0, 0.85); }
.card:hover > .prompt-save-overlay { display: block; }
/* Open-folder button in the controls bar, same size and look as the one of the LoRA/Checkpoints tabs (30 x 30 px) */
.prompt-save-open-folder { flex: 0 0 auto; align-self: center; display: inline-flex; align-items: center; justify-content: center; width: 30px; min-width: 30px; max-width: 30px; height: 30px; padding: 0; margin: 0 6px 0 0; font-size: 14px; line-height: 1; cursor: pointer; color: var(--button-secondary-text-color, var(--body-text-color)); background: var(--button-secondary-background-fill, var(--background-fill-secondary)); border: 1px solid var(--button-secondary-border-color, var(--border-color-primary)); border-radius: var(--button-small-radius, var(--radius-md, 6px)); box-shadow: var(--button-shadow, none); transition: background 0.15s ease, border-color 0.15s ease; }
.prompt-save-open-folder:hover { background: var(--button-secondary-background-fill-hover, var(--background-fill-primary)); border-color: var(--button-secondary-border-color-hover, var(--border-color-accent, var(--border-color-primary))); }
.prompt-save-open-folder:active { filter: brightness(0.92); }
/* Edit dialog: the file table sits vertically centered in the free space of the left column (next to the preview) */
.edit-user-metadata .row:has(.prompt-save-filedata) { align-items: stretch !important; }
.edit-user-metadata .prompt-save-filedata { margin-top: auto !important; margin-bottom: auto !important; flex-grow: 0 !important; }
/* The CivitAI Browser extension adds its model-info button to every card, which makes no sense for prompts */
.card:has(> .prompt-save-overlay) .goto-civitbrowser { display: none !important; }
</style>"""


# ----------------------------------------------------------------------------------------------
# Small helper functions
# ----------------------------------------------------------------------------------------------

def show_element():
    # Result for a gradio element that should become visible.
    return gr.update(visible=True)


def hide_element():
    # Result for a gradio element that should become hidden.
    return gr.update(visible=False)


def clean_name(text):
    # Keeps only letters, digits, spaces, "_" and "-" (this is allowed in file and folder names).
    cleaned = ""
    for character in text:
        if character.isalnum() or character in (" ", "_", "-"):
            cleaned += character
    return cleaned.strip()


def folder_below(text):
    # Returns the subfolder of the prompt folder that belongs to the text of the search box
    # (e.g. "Blasian\" or free text like "linux penguin"), as a path with "/" as separator.
    # An empty string means: the prompt folder itself.
    # Every part of the text is checked: an existing subfolder is used as it is,
    # otherwise the part becomes the name of a NEW folder (it is created later when saving).
    prompts_dir = get_prompts_dir()
    if prompts_dir is None:
        return ""

    parts = []
    current_folder = prompts_dir
    current_exists = True  # becomes False as soon as one part is a new folder
    for part in (text or "").replace("\\", "/").split("/"):
        part = part.strip()
        if part == "":
            continue

        # (the check with listdir makes sure that parts like ".." or "C:" are never accepted)
        if current_exists and part in os.listdir(current_folder) and os.path.isdir(os.path.join(current_folder, part)):
            pass  # an existing folder: use it as it is
        else:
            part = clean_name(part)  # a new folder: only allowed characters
            if part == "":
                continue
            current_exists = False

        parts.append(part)
        current_folder = os.path.join(current_folder, part)

    return "/".join(parts)


# ----------------------------------------------------------------------------------------------
# Reading values from the txt2img / img2img tabs (used to pre-fill the "Save New Prompt" dialog)
# ----------------------------------------------------------------------------------------------

# Fields of the tab (the part after "<tab>_") that are written into the Notes, in this order
TAB_SETTINGS = [
    ("sampling", "Sampling Method"),
    ("scheduler", "Schedule Type"),
    ("steps", "Sampling Steps"),
    ("width", "Width"),
    ("height", "Height"),
    ("cfg_scale", "CFG Scale"),
    ("seed", "Seed"),
]

# The elem_ids of all components we need from the txt2img / img2img tabs
WATCHED_IDS = []
for tab in ("txt2img", "img2img"):
    WATCHED_IDS.append(f"{tab}_prompt")
    WATCHED_IDS.append(f"{tab}_neg_prompt")
    WATCHED_IDS.append(f"{tab}_gallery")
    WATCHED_IDS.append(f"generation_info_{tab}")
    for key, label in TAB_SETTINGS:
        WATCHED_IDS.append(f"{tab}_{key}")

# elem_id -> component, filled by on_after_component
prompt_components = {}


def on_after_component(component, **kwargs):
    # Called by the WebUI for every component it creates: we remember the ones we need.
    elem_id = kwargs.get("elem_id")
    if elem_id in WATCHED_IDS:
        prompt_components[elem_id] = component


script_callbacks.on_after_component(on_after_component)


def header_settings_lines():
    # Settings from the header of the WebUI (preset, VAE, checkpoint ...) as text lines.
    # The header is created after the txt2img/img2img tabs, so the values are read from the options.
    opts = shared.opts

    modules = opts.forge_additional_modules or []
    if isinstance(modules, str):
        modules = [modules]
    module_names = []
    for module in modules:
        module_names.append(os.path.basename(str(module)))

    return [
        f"UI Preset: {opts.forge_preset}",
        f"VAE / Text Encoder: {', '.join(module_names)}",
        f"Diffusion in Low Bits: {opts.forge_unet_storage_dtype}",
        f"Clip Skip: {opts.CLIP_stop_at_last_layers}",
        f"Checkpoint: {opts.sd_model_checkpoint}",
    ]


def merge_note_lines(text):
    # Joins line pairs of the notes text that belong together:
    # "Sampling Method: DPM++ 2M" + "Schedule Type: Karras" -> "Sampling/Scheduler: DPM++ 2M Karras"
    # "Width: 1024" + "Height: 1536"                        -> "Width x height: 1024 x 1536"
    lines = (text or "").splitlines()
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # The next line (empty if there is none)
        next_line = ""
        if i + 1 < len(lines):
            next_line = lines[i + 1]

        if line.startswith("Sampling Method: ") and next_line.startswith("Schedule Type: "):
            sampler = line[len("Sampling Method: "):]
            scheduler = next_line[len("Schedule Type: "):]
            result.append(f"Sampling/Scheduler: {sampler} {scheduler}")
            i += 2  # two lines were used
        elif line.startswith("Width: ") and next_line.startswith("Height: "):
            width = line[len("Width: "):]
            height = next_line[len("Height: "):]
            result.append(f"Width x Height: {width} x {height}")
            i += 2
        else:
            result.append(line)
            i += 1

    return "\n".join(result)


def build_notes(tab_values):
    # The Notes text: settings of the header + settings of the tab, one per line.
    lines = header_settings_lines()
    for (key, label), value in zip(TAB_SETTINGS, tab_values):
        lines.append(f"{label}: {value}")
    return merge_note_lines("\n".join(lines))


def fill_add_dialog(prompt, negative_prompt, gallery_images, index, generation_info, search_text, *tab_values):
    # Opens the "Save New Prompt" dialog and pre-fills it with the values of the current tab.
    # The returned values go to the outputs of btn_open_add_prompt in create_ui(), in the same order.

    # The image: the one selected in the gallery (index), or the first one
    image = None
    geninfo = ""
    if gallery_images:
        if index < 0 or index >= len(gallery_images):
            index = 0
        image = gallery_images[index]
        if isinstance(image, (tuple, list)):
            image = image[0]
        # The generation info (infotext) of that image, as the gallery's own save button uses it
        try:
            geninfo = json.loads(generation_info)["infotexts"][index]
        except Exception:
            geninfo = ""

    return (
        show_element(),  # add_box: show the dialog
        "",  # add_name: always empty
        prompt,
        negative_prompt,
        "",  # add_description: always empty
        build_notes(tab_values),
        image,
        geninfo,
        folder_below(search_text),  # add_folder: the folder selected with the folder buttons
        hide_element(),  # add_confirm: hide the overwrite question
        "",  # add_status: no message
    )


# ----------------------------------------------------------------------------------------------
# EXIF data of the preview image (shown when the mouse is over a card)
# ----------------------------------------------------------------------------------------------

# preview path -> (modification time, text): we only read an image again if the file changed
exif_cache = {}


def read_exif_info(preview_path):
    # Returns the generation info (EXIF) of a card's preview image as text.
    try:
        mtime = os.path.getmtime(preview_path)
    except OSError:
        return "No preview image"

    cached = exif_cache.get(preview_path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        with Image.open(preview_path) as image:
            geninfo, _ = images.read_info_from_image(image)
        text = geninfo or "No EXIF data"
    except Exception as e:
        text = f"Could not read EXIF data: {e}"

    exif_cache[preview_path] = (mtime, text)
    return text


# ----------------------------------------------------------------------------------------------
# The edit dialog of a card ("Edit metadata") and the "Save New Prompt" dialog
# ----------------------------------------------------------------------------------------------

class PromptSaveUserMetadataEditor(UserMetadataEditor):
    def refresh_button_id(self):
        # The id of the (hidden) refresh button of the tab: a click on it reloads the cards.
        return f"{self.tabname}_{self.page.extra_networks_tabname}_extra_refresh_internal"

    def create_default_editor_elems(self):
        # Same layout as in the base class; the only difference is the css class of the table (see OVERLAY_CSS)
        with gr.Row():
            with gr.Column(scale=2):
                self.edit_name = gr.HTML(elem_classes="extra-network-name")
                self.edit_description = gr.Textbox(label="Description", lines=1.05, max_lines=4)
                self.html_filedata = gr.HTML(elem_classes="prompt-save-filedata")

                self.create_extra_default_items_in_left_column()

            with gr.Column(scale=1, min_width=0):
                self.html_preview = gr.HTML()

    def get_metadata_table(self, name):
        # The table with file infos. The base class lists Filename, File size, Hash and Modified.
        # Here "Filename" shows only the file name (without subfolders) and "File size" is replaced by "Folder path".
        card_file = self.page.items.get(name, {}).get("filename")

        table = []
        for label, value in super().get_metadata_table(name):
            if label.strip() == "File size:":
                table.append(("Folder path: ", get_relative_prompts_dir(card_file)))
            elif label.strip() == "Filename:" and card_file:
                table.append((label, os.path.basename(card_file)))
            else:
                table.append((label, value))
        return table

    def put_values_into_components(self, name):
        # Fills the dialog when it opens. The returned values go to viewed_components in create_editor().
        user_metadata = self.get_user_metadata(name)
        values = list(super().put_values_into_components(name))

        # Source for the "Send to" buttons: always the prompt and negative prompt of the card
        geninfo = f"{user_metadata.get('prompt', '')}\nNegative prompt: {user_metadata.get('negative_prompt', '')}"

        # The preview image is only used as init image for img2img
        image = None
        try:
            with Image.open(self.page.items.get(name, {}).get("local_preview", "")) as img:
                image = img.copy()
        except Exception:
            pass  # no preview image: that is fine

        return values + [
            user_metadata.get("prompt", ""),
            user_metadata.get("negative_prompt", ""),
            geninfo,
            image,
        ]

    def save_prompt_user_metadata(self, name, desc, prompt, negative_prompt, notes):
        # Called by the Save button of the dialog: writes the changed fields into the json file of the card.
        user_metadata = self.get_user_metadata(name)
        user_metadata["description"] = desc
        user_metadata["prompt"] = prompt
        user_metadata["negative_prompt"] = negative_prompt
        user_metadata["notes"] = notes

        self.write_user_metadata(name, user_metadata)

        # Update the cached card too, so that the card and its hover overlay show the new values
        item = self.page.items.get(name)
        if item is not None:
            item["description"] = desc
            item["prompt"] = quote_js(prompt)
            item["negative_prompt"] = quote_js(negative_prompt)

    def create_default_buttons(self):
        # Buttons of the dialog: Cancel, Save, Send to txt2img / img2img and Delete.
        with gr.Row(elem_classes="edit-user-metadata-buttons"):
            self.button_cancel = gr.Button("Cancel")
            self.button_save = gr.Button("Save", variant="primary")
            self.button_send_txt2img = gr.Button("Send to txt2img", variant="primary")
            self.button_send_img2img = gr.Button("Send to img2img", variant="primary")
            self.button_delete = gr.Button("Delete", variant="stop")

        # The question that is shown after a click on "Delete"
        with gr.Row(visible=False) as self.delete_confirm:
            gr.HTML("<b>Do you really want do delete this prompt?</b>")
            self.button_delete_no = gr.Button("No")
            self.button_delete_yes = gr.Button("Yes", variant="stop")

        self.html_status = gr.HTML(elem_classes="edit-user-metadata-status")
        self.edit_geninfo = gr.Textbox(visible=False)  # hidden: text for the "Send to" buttons
        self.edit_image = gr.Image(type="pil", visible=False)  # hidden: image for the "Send to" buttons

        self.button_cancel.click(fn=None, _js="closePopup")

    def delete_prompt(self, name):
        # Deletes a card. Nothing is really deleted: the files are renamed to <name>_old_<timestamp>.<ext>.bak
        # (the same as when a prompt is overwritten), so they can be restored by hand.
        item = self.page.items.get(name, {})
        timestamp = time.strftime("%Y%m%d_%H%M%S")

        for path in (item.get("filename"), item.get("local_preview")):
            if path and os.path.isfile(path):
                base, ext = os.path.splitext(path)
                os.replace(path, f"{base}_old_{timestamp}{ext}.bak")
                try:
                    self.page.lister.update_file_entry(path)
                except Exception:
                    pass  # only the file list of the WebUI could not be updated

        self.page.items.pop(name, None)

    def setup_ui(self, gallery):
        # Not needed here: the base class would add buttons that we do not use
        pass

    def show_dialog(self):
        # Shows the dialog and hides the delete question
        return show_element(), hide_element()

    def create_editor(self):
        # Builds the edit dialog and connects the buttons.
        self.create_default_editor_elems()

        self.edit_prompt = gr.TextArea(label="Prompt", lines=4)
        self.edit_negative_prompt = gr.TextArea(label="Negative Prompt", lines=4)
        self.edit_notes = gr.TextArea(label="Notes", lines=4)

        self.create_default_buttons()

        # The components that are filled by put_values_into_components (same order as the returned values!)
        viewed_components = [
            self.edit_name,
            self.edit_description,
            self.html_filedata,
            self.html_preview,
            self.edit_notes,
            self.edit_prompt,
            self.edit_negative_prompt,
            self.edit_geninfo,
            self.edit_image,
        ]

        # The "Edit metadata" button of a card: fill the dialog, then show it
        self.button_edit.click(
            fn=self.put_values_into_components,
            inputs=[self.edit_name_input],
            outputs=viewed_components,
        ).then(
            fn=self.show_dialog,
            inputs=[],
            outputs=[self.box, self.delete_confirm],
        )

        # Save: writes the fields, closes the dialog and refreshes the card
        self.setup_save_handler(
            self.button_save,
            self.save_prompt_user_metadata,
            [self.edit_description, self.edit_prompt, self.edit_negative_prompt, self.edit_notes],
        )

        # Send to txt2img / img2img: same mechanism as the buttons below the gallery (text -> fields)
        self.connect_send_button(self.button_send_txt2img, "txt2img")
        self.connect_send_button(self.button_send_img2img, "img2img")

        # Delete: first only the question is shown. "No" hides it again, "Yes" deletes the card
        self.button_delete.click(fn=show_element, outputs=[self.delete_confirm])
        self.button_delete_no.click(fn=hide_element, outputs=[self.delete_confirm])
        self.button_delete_yes.click(
            fn=self.delete_prompt,
            inputs=[self.edit_name_input],
            outputs=[],
        ).then(
            fn=None,
            _js=f"function(){{ closePopup(); document.getElementById('{self.refresh_button_id()}').click(); }}",
        )

    def connect_send_button(self, button, target_tab):
        # Makes a "Send to ..." button paste the prompt into the fields of target_tab ("txt2img" or "img2img").
        binding = infotext_utils.ParamBinding(
            paste_button=button,
            tabname=target_tab,
            source_text_component=self.edit_geninfo,
            source_image_component=self.edit_image,
        )
        infotext_utils.register_paste_params_button(binding)
        # Close the dialog and switch to the tab
        button.click(fn=None, _js=f"function(){{ closePopup(); switch_to_{target_tab}(); }}")

    def create_ui(self):
        # Builds the edit dialog (base class) and the "Save New Prompt" dialog.
        super().create_ui()

        prefix = f"{self.tabname}_{self.page.extra_networks_tabname}"

        # Hidden buttons: the visible buttons in the controls bar click them (see create_html)
        self.btn_open_add_prompt = gr.Button(visible=False, elem_id=f"{prefix}_add_prompt_btn")
        self.btn_open_folder = gr.Button(visible=False, elem_id=f"{prefix}_open_folder_btn")
        self.btn_open_folder.click(fn=open_prompts_folder, inputs=[], outputs=[], show_progress=False)

        # The "Save New Prompt" dialog
        with gr.Group(visible=False, elem_id=f"{prefix}_add_prompt_modal", elem_classes="edit-user-metadata") as add_box:
            self.add_box = add_box
            gr.HTML("<h2>Save New Prompt</h2>")
            self.add_name = gr.Textbox(label="Name", placeholder="Name of the prompt")
            self.add_description = gr.TextArea(label="Description", lines=2)
            self.add_prompt = gr.TextArea(label="Prompt", lines=4)
            self.add_negative_prompt = gr.TextArea(label="Negative Prompt", lines=4)
            self.add_notes = gr.TextArea(label="Notes", lines=10)

            # The preview image has the same size as a card: the size from the settings (px), else 16rem x 24rem like in style.css
            if shared.opts.extra_networks_card_width:
                card_w = f"{shared.opts.extra_networks_card_width}px"
            else:
                card_w = "16rem"
            if shared.opts.extra_networks_card_height:
                card_h = f"{shared.opts.extra_networks_card_height}px"
            else:
                card_h = "24rem"
            image_id = f"{prefix}_add_image"
            gr.HTML(f"<style>#{image_id} {{ width: {card_w} !important; min-width: {card_w} !important; max-width: {card_w} !important; height: {card_h} !important; flex: none !important; }}</style>")
            self.add_image = gr.Image(label="Preview Image", type="pil", interactive=True, elem_id=image_id)

            with gr.Row():
                self.add_cancel = gr.Button("Cancel", elem_id=f"{prefix}_add_cancel_btn")
                self.add_save = gr.Button("Save", variant="primary")
            self.add_status = gr.HTML()

            # Hidden helper fields
            self.add_saved = gr.Checkbox(visible=False)  # True after a successful save
            self.add_geninfo = gr.Textbox(visible=False)  # infotext of the image (written as EXIF)
            self.add_folder = gr.Textbox(visible=False)  # target subfolder, set when the dialog opens
            self.add_folder_input = gr.Textbox(visible=False)  # receives the text of the search box from the browser

            # The question "overwrite?"
            with gr.Row(visible=False) as add_confirm:
                self.add_confirm = add_confirm
                self.add_overwrite_cancel = gr.Button("No")
                self.add_overwrite = gr.Button("Yes", variant="stop")

        # The "+" button opens the dialog. The JavaScript (_js) runs in the browser first: it replaces the button value
        # with the index of the selected gallery image and the search box text with its current value.
        read_search_box = f"(gradioApp().querySelector('#{prefix}_extra_search') || {{}}).value || ''"
        self.btn_open_add_prompt.click(
            fn=fill_add_dialog,
            _js=f"(p, n, g, i, gi, s, ...rest) => [p, n, g, selected_gallery_index(), gi, {read_search_box}, ...rest]",
            inputs=[
                prompt_components[f"{self.tabname}_prompt"],
                prompt_components[f"{self.tabname}_neg_prompt"],
                prompt_components[f"{self.tabname}_gallery"],
                self.btn_open_add_prompt,
                prompt_components[f"generation_info_{self.tabname}"],
                self.add_folder_input,
            ] + [prompt_components[f"{self.tabname}_{key}"] for key, label in TAB_SETTINGS],
            outputs=[
                self.add_box, self.add_name, self.add_prompt, self.add_negative_prompt, self.add_description,
                self.add_notes, self.add_image, self.add_geninfo, self.add_folder, self.add_confirm, self.add_status,
            ],
        )

        self.add_cancel.click(fn=hide_element, outputs=[self.add_box]).then(fn=None, _js="closePopup")

        # "Save" and "Yes" (overwrite) do the same, only with a different function
        self.connect_save_button(self.add_save, save_prompt)
        self.connect_save_button(self.add_overwrite, save_prompt_overwrite)

        self.add_overwrite_cancel.click(fn=self.hide_overwrite_question, outputs=[self.add_confirm, self.add_status])

    def hide_overwrite_question(self):
        # Hides the question "overwrite?" and clears the status message
        return hide_element(), ""

    def show_unless_saved(self, saved):
        # The dialog stays visible if the prompt was not saved (so the error message can be read)
        return gr.update(visible=not saved)

    def connect_save_button(self, button, save_function):
        # Runs save_function when the button is clicked. Only if the prompt was really saved,
        # the dialog is closed and the list of cards is refreshed.
        inputs = [
            self.add_name, self.add_prompt, self.add_negative_prompt, self.add_description,
            self.add_notes, self.add_image, self.add_geninfo, self.add_folder,
        ]

        button.click(
            fn=save_function,
            inputs=inputs,
            outputs=[self.add_status, self.add_confirm, self.add_saved],
        ).then(
            fn=self.show_unless_saved,
            inputs=[self.add_saved],
            outputs=[self.add_box],
        ).then(
            fn=None,
            inputs=[self.add_saved],
            _js=f"function(saved){{ if (saved) {{ closePopup(); document.getElementById('{self.refresh_button_id()}').click(); }} }}",
        )


# ----------------------------------------------------------------------------------------------
# The "Prompts" tab in Extra Networks
# ----------------------------------------------------------------------------------------------

class ExtraNetworksPagePromptSave(ui_extra_networks.ExtraNetworksPage):
    def __init__(self):
        super().__init__("Prompts")
        self.allow_negative_prompt = True

    def refresh(self):
        # Nothing to do: list_items() reads the files again every time
        pass

    def create_html(self, tabname, *, empty=False):
        # The HTML of the tab. We take the normal HTML and add our buttons and styles.
        page_html = super().create_html(tabname, empty=empty)
        prefix = f"{tabname}_{self.extra_networks_tabname}"

        # Error message (red) if the prompt folder cannot be used
        error_html = ""
        if get_prompts_dir() is None:
            error_html = f"<div style='color:red; padding: 0.5em;'>{html.escape(dir_error['text'])}</div>"

        # The "+" button: opens the "Save New Prompt" dialog (it clicks the hidden button of create_ui)
        add_button = f'''
        <div id="{prefix}_extra_add" class="extra-network-control--refresh"
            style="display: flex; justify-content: center; align-items: center;"
            title="Add new prompt"
            onclick="popupId('{prefix}_add_prompt_modal'); document.getElementById('{prefix}_add_prompt_btn').click();">
            <span style="display: flex; align-items: center; justify-content: center; width: 1.5rem; height: 1.5rem; font-size: 1.5rem; font-weight: bold; line-height: 1; color: var(--button-secondary-text-color);">+</span>
        </div>
        '''

        # The folder button: opens the prompt folder (it clicks the hidden button of create_ui)
        folder_button = f'''<button type="button" class="prompt-save-open-folder" title="Open prompts folder"
            onclick="document.getElementById('{prefix}_open_folder_btn').click();">📁</button>
        '''

        # Put the folder button in front of the search box, and the "+" button in front of the refresh button
        page_html = page_html.replace('<div class="extra-network-control--search">', folder_button + '<div class="extra-network-control--search">', 1)
        page_html = page_html.replace(
            f'<div id="{prefix}_extra_refresh"',
            add_button + f'\n        <div id="{prefix}_extra_refresh"',
        )
        return OVERLAY_CSS + error_html + page_html

    def create_item_html(self, tabname, item, template=None):
        # The HTML of one card.
        # A click on the card clicks its own "Edit metadata" button (no prompt is applied).
        # Clicks on the buttons of the card itself are ignored, otherwise the edit button would trigger itself again.
        click_js = (
            "if (event.target.closest('.card-button')) return; "
            "var b = this.querySelector('.edit-button'); if (b) b.click();"
        )
        item = dict(item)  # a copy, so that the original item is not changed
        item["onclick"] = html.escape(click_js)
        result = super().create_item_html(tabname, item, template)

        # Add the hover overlay (EXIF data of the preview) in front of the last </div> of the card
        if isinstance(result, str) and "</div>" in result:
            exif_text = read_exif_info(item["local_preview"])
            overlay = f'<div class="prompt-save-overlay">{html.escape(exif_text)}</div>'
            end = result.rfind("</div>")
            result = result[:end] + overlay + result[end:]

        return result

    def list_items(self):
        # Yields all cards: every .json file in the prompt folder and its subfolders.
        prompts_dir = get_prompts_dir()
        if prompts_dir is None:
            return

        index = 0
        for folder, subfolders, filenames in os.walk(prompts_dir):
            subfolders.sort()  # os.walk then visits the subfolders in this order
            for filename in sorted(filenames):
                if not filename.endswith(".json"):
                    continue
                # The name of a card is its path below the prompt folder, without ".json" (e.g. "sub/name")
                relative = os.path.relpath(os.path.join(folder, filename), prompts_dir)
                name = os.path.splitext(relative)[0].replace(os.sep, "/")
                item = self.create_item(name, index)
                if item is not None:
                    yield item
                index += 1

    def create_item(self, name, index=None, enable_filter=True):
        # Reads the json file of one card and returns the data for it (None if it cannot be read).
        prompts_dir = get_prompts_dir()
        if prompts_dir is None:
            return None

        # The file is always <prompt folder>/<name>.json
        file_path = os.path.normpath(os.path.join(prompts_dir, f"{name}.json"))
        # The name comes from the browser: it must never point outside of the prompt folder
        if os.path.commonpath([prompts_dir, file_path]) != prompts_dir or not os.path.isfile(file_path):
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None

        path_without_ext = os.path.splitext(file_path)[0]

        item = {
            "name": name,
            "filename": file_path,
            "preview": self.find_preview(path_without_ext),
            "description": data.get("description", ""),
            "search_terms": [name, self.search_terms_from_path(file_path)],
            "local_preview": existing_preview_path(path_without_ext),
            "prompt": quote_js(data.get("prompt", "")),
            "negative_prompt": quote_js(data.get("negative_prompt", "")),
            "sort_keys": {"default": index, **self.get_sort_keys(file_path)},
        }

        # Adds the user data of the json file (description, notes ...) to the item
        self.read_user_metadata(item)
        return item

    def allowed_directories_for_previews(self):
        # Folders from which preview images may be shown.
        # This must never raise an error: it runs while the pages are registered, an error would break the other tabs too
        prompts_dir = get_prompts_dir()
        if prompts_dir is None:
            return []
        return [prompts_dir]

    def create_user_metadata_editor(self, ui, tabname):
        return PromptSaveUserMetadataEditor(ui, tabname, self)


# ----------------------------------------------------------------------------------------------
# Saving a new prompt
# ----------------------------------------------------------------------------------------------

PREVIEW_EXTENSIONS = (".png", ".jpg")


def preview_extension():
    # The preview format follows the WebUI setting "Saving images/grids" -> "Image Format":
    # png -> ".png", everything else (jpg/jpeg) -> ".jpg" (the quality comes from "JPEG Quality")
    if str(shared.opts.samples_format).lower() == "png":
        return ".png"
    return ".jpg"


def existing_preview_path(path_without_ext):
    # Returns the preview file of a card (.png or .jpg, whichever exists).
    # If there is none yet, the path in the currently set format is returned.
    for ext in PREVIEW_EXTENSIONS:
        if os.path.isfile(path_without_ext + ext):
            return path_without_ext + ext
    return path_without_ext + preview_extension()


def save_prompt(name, prompt, negative_prompt, description, notes, image, geninfo, folder, overwrite=False):
    # Saves a prompt as <folder>/<name>.json (+ <name>.png or <name>.jpg as preview).
    # Returns (status html, update for the overwrite question, True if it was saved).
    if not name:
        return "<div style='color:red;'>Please provide a name for the prompt.</div>", hide_element(), False

    # The file name: only letters, digits, spaces, "_" and "-"
    safe_name = clean_name(name)
    if safe_name == "":
        safe_name = "unnamed_prompt"

    prompts_dir = get_prompts_dir()
    if prompts_dir is None:
        return f"<div style='color:red;'>{html.escape(dir_error['text'])}</div>", hide_element(), False

    # The target folder is the one from the search box when the dialog was opened (checked again here;
    # a folder that does not exist yet is created below). Without it: the prompt folder itself.
    relative_folder = folder_below(folder)
    if relative_folder:
        target_dir = os.path.join(prompts_dir, relative_folder)
        shown_name = f"{relative_folder}/{safe_name}"
    else:
        target_dir = prompts_dir
        shown_name = safe_name

    file_path = os.path.join(target_dir, f"{safe_name}.json")

    # The card exists already: ask first (the "Yes" button calls this function again with overwrite=True)
    if os.path.exists(file_path) and not overwrite:
        message = f"<div style='color:orange;'>Card with name {html.escape(shown_name)} already exists, overwrite?</div>"
        return message, show_element(), False

    data = {
        "name": name,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "description": description,
        "notes": notes,
    }

    try:
        os.makedirs(target_dir, exist_ok=True)

        # An existing card is kept as <name>_old_<timestamp>.json.bak before it is replaced
        if os.path.exists(file_path):
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            os.replace(file_path, os.path.join(target_dir, f"{safe_name}_old_{timestamp}.json.bak"))

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

        # The preview is always replaced by the current image, with the generation info (EXIF / PNG info).
        # Format and JPEG quality come from the WebUI settings (the same function the gallery save button uses)
        if image is not None:
            new_ext = preview_extension()
            # A preview in the other format would be found first and hide the new one: keep it as .bak
            for ext in PREVIEW_EXTENSIONS:
                old_preview = os.path.join(target_dir, f"{safe_name}{ext}")
                if ext != new_ext and os.path.isfile(old_preview):
                    timestamp = time.strftime("%Y%m%d_%H%M%S")
                    os.replace(old_preview, os.path.join(target_dir, f"{safe_name}_old_{timestamp}{ext}.bak"))
            images.save_image_with_geninfo(image, geninfo or None, os.path.join(target_dir, f"{safe_name}{new_ext}"))

        return f"<div style='color:green;'>Successfully saved prompt as {html.escape(shown_name)}</div>", hide_element(), True
    except Exception as e:
        return f"<div style='color:red;'>Failed to save prompt: {str(e)}</div>", hide_element(), False


def save_prompt_overwrite(name, prompt, negative_prompt, description, notes, image, geninfo, folder):
    # Like save_prompt, but replaces an existing card (the "Yes" button of the overwrite question).
    return save_prompt(name, prompt, negative_prompt, description, notes, image, geninfo, folder, overwrite=True)


def on_before_ui():
    # Registers the "Prompts" tab.
    ui_extra_networks.register_page(ExtraNetworksPagePromptSave())


script_callbacks.on_before_ui(on_before_ui)

