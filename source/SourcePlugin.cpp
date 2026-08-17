#include "Plugin.h"

/**
	The source registration.

	**This file is listed directly in the AmberSource target, not in any static
	library.** `CFFGLPluginInfo` registers itself from a file-scope constructor
	and nothing ever references it by name, so in a STATIC archive the linker is
	entitled to drop the whole translation unit -- producing a bundle that
	loads, exports `plugMain`, and reports that it contains no plugins at all.

		nm -gU Amber.bundle/Contents/MacOS/Amber | grep plugMain
*/
static CFFGLPluginInfo PluginInfo(
	PluginFactory< amber::AmberPlugin >,          // Create method
	"AM01",                                       // Plugin unique ID, max 4 chars
	"Amber",                                      // Plugin name
	2,                                            // API major version
	1,                                            // API minor version
	0,                                            // Plugin major version
	1,                                            // Plugin minor version
	FF_SOURCE,                                    // Plugin type
	"Flash content as a live source",             // Description
	"Amber FFGL source -- powered by Ruffle"      // About
);

extern "C" const char* AmberBuildStamp()
{
	return "amber " AMBER_VERSION " source, built " __DATE__ " " __TIME__;
}
