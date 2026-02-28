# -*- coding: UTF-8 -*-

# Build customizations
# Change this file instead of sconstruct or manifest files, whenever possible.

# Full getext (please don't change)
_ = lambda x : x

# Add-on information variables
addon_info = {
	# for previously unpublished addons, please follow the community guidelines at:
	# https://bitbucket.org/nvdaaddonteam/todo/src/56140dbec531e4d7591338e1dbc6192f3dd422a8/guideLines.txt
	# add-on Name, internal for nvda
	"addon_name" : "Unspoken-ng",
	# Add-on summary, usually the user visible name of the addon.
	# TRANSLATORS: Summary for this add-on to be shown on installation and add-on information.
	"addon_summary" : _("Unspoken-ng 3D Audio"),
	# Add-on description
	# Translators: Long description to be shown for this add-on on add-on information from add-ons manager
	"addon_description" : _("""Adds 3D audio for controls and replaces control messages. This updated version uses steamaudio and is only compatible with NVDA 2026.1 and later."""),
	# version
	"addon_version" : "2.0",
	# Author(s)
	"addon_author" : "Camlorn <camlorn38@gmail.com>, Bryan Smart< Bryansmart@bryansmart.com>, Masonasons <mason@masonasons.me>, Tyler Spivey, Samuel Proulx, Ambro86, akj",
	# URL for the add-on documentation support
	"addon_url" : "https://github.com/fastfinge/unspoken-ng",
	# Documentation file name
	"addon_docFileName" : "readme.html",
	"addon_minimum_nvda_version" : "2026.1",
	"addon_last_tested_nvda_version" : "2026.1",
}


import os.path

# Define the python files that are the sources of your add-on.
# You can use glob expressions here, they will be expanded.
pythonSources = []
#If you translate this, change this to not include wav files in the list of translated files.
for dirpath, dirnames, filenames in os.walk(os.path.join("addon", "globalPlugins")):
		pythonSources.extend([os.path.join(dirpath, fi) for fi in filenames if fi.endswith(".py") or fi.endswith(".wav")])

# Files that contain strings for translation. Usually your python sources
i18nSources = pythonSources + ["buildVars.py", "docHandler.py"]

# Files that will be ignored when building the nvda-addon file
# Paths are relative to the addon directory, not to the root directory of your addon sources.
excludedFiles = []
