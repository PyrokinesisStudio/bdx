import os
import sys
import bpy
import time
import subprocess
import threading
from .. import utils as ut

runThread = None
export_time = None

class RunThread(threading.Thread):

    def run(self):

        gradlew = "gradlew"
        if os.name != "posix":
            gradlew += ".bat"

        print(" ")
        print("------------ BDX START --------------------------------------------------")
        print(" ")
        try:
            subprocess.check_call([os.path.join(ut.project_root(), gradlew), "-p", ut.project_root(), "desktop:run"])
        except subprocess.CalledProcessError:
            pass
            # Can't call operator.report() for an error in a thread
        print(" ")
        print("------------ BDX END ----------------------------------------------------")
        print(" ")

def export(self, context, multiBlend, diffExport):

    global runThread, export_time

    # Set the mouse cursor to "WAIT" as soon as exporting starts
    context.window.cursor_set("WAIT")
    
    j = os.path.join

    proot = ut.project_root()
    sroot = ut.src_root()
    asset_dir = j(proot, "android", "assets", "bdx")
    prof_scene_name = "__Profiler"
    bdx_scenes_dir = j(asset_dir, "scenes")
    bdx_blend_dir = j(proot, "blender")
    current_blend_name = os.path.split(bpy.data.filepath)[1]

    # Determine which scenes need to be exported

    blends = os.listdir(bdx_blend_dir)

    scenes_for_export = []

    prev_scenes = bpy.data.scenes[:]
    prev_texts = bpy.data.texts[:]
    linked_scenes = []
    linked_texts = []
    multipleBlendFilesExported = False

    if not multiBlend or (not diffExport and os.path.isdir(bdx_scenes_dir)):    # Delete old scene files except for the profiler.
        old_scenes = ut.listdir(bdx_scenes_dir)
        for f in old_scenes:
            if os.path.basename(f) != prof_scene_name + ".bdx":
                os.remove(f)

    scenes_for_export += bpy.data.scenes

    if multiBlend:

        for blendName in blends:

            blend_path = j(bdx_blend_dir, blendName)

            if current_blend_name != blendName:

                if not diffExport or (export_time is None or os.path.getmtime(blend_path) > export_time):

                    if os.path.splitext(blendName)[1] == ".blend":    # We don't want backups (.blend1, .blend2)

                        multipleBlendFilesExported = True

                        with bpy.data.libraries.load(blend_path, link=True) as (data_from, data_to):
                            data_to.scenes += data_from.scenes          # Link scenes from other blends
                            data_to.texts += data_from.texts

    # Check if profiler scene needs export:
    prof_scene_export = prof_scene_name + ".bdx" not in os.listdir(bdx_scenes_dir)

    if prof_scene_export:
        with bpy.data.libraries.load(j(ut.gen_root(), "profiler.blend")) as (data_from, data_to):
            data_to.scenes = ['__Profiler']

    for scene in bpy.data.scenes:
        if scene not in prev_scenes:
            linked_scenes.append(scene)
            scenes_for_export.append(scene)

    for text in bpy.data.texts:
        if text not in prev_texts:
            linked_texts.append(text)

    # Save-out internal java files
    ut.save_internal_java_files(ut.src_root())

    if not diffExport or not multiBlend:          # Clear inst dir (files generated by export_scene) if we're doing a full export or just one scene

        inst = j(ut.src_root(), "inst")
        if os.path.isdir(inst):
            inst_files = ut.listdir(inst)
            for f in inst_files:
                os.remove(f)
        else:
            os.mkdir(inst)

    # Export scenes:
    for i in range(len(scenes_for_export)):
        scene = scenes_for_export[i]
        file_name = scene.name + ".bdx"
        file_path = j(asset_dir, "scenes", file_name)
        sys.stdout.write("\rBDX - Exporting Scene: {0} ({1}/{2})                            ".format(scene.name, i+1, len(scenes_for_export)))
        sys.stdout.flush()
        bpy.ops.export_scene.bdx(filepath=file_path, scene_name=scene.name, exprun=True)

    sys.stdout.write("\rBDX - Export Finished                                 ")      # Erase earlier text with this print
    sys.stdout.flush()

    print("")

    # Modify relevant files:
    bdx_app = j(sroot, "BdxApp.java")

    # - BdxApp.java
    new_lines = []
    for s in ut.listdir(bdx_scenes_dir):                    # Create an i<SceneName> instantiator for each scene .bdx
        name = os.path.splitext(os.path.basename(s))[0]
        class_name = ut.str_to_valid_java_class_name(name)
        if os.path.isfile(j(sroot, "inst", class_name + ".java")):
            inst = "new " + ut.package_name() + ".inst." + class_name + "()"
        else:
            inst = "null"

        new_lines.append('("{}", {});'.format(name, inst))

    for scene in linked_scenes:
        version = float("{}.{}".format(*bpy.app.version))
        if version >= 2.78:
            bpy.data.scenes.remove(scene, True)
        else:
            bpy.data.scenes.remove(scene)

    for text in linked_texts:
        version = float("{}.{}".format(*bpy.app.version))
        if version >= 2.78:
            bpy.data.texts.remove(text, True)
        else:
            bpy.data.texts.remove(text)

    put = "\t\tScene.instantiators.put"

    ut.remove_lines_containing(bdx_app, put)

    ut.insert_lines_after(bdx_app, "Scene.instantiators =", [put + l for l in new_lines])

    current_scene = bpy.context.scene

    first_scene = bpy.context.scene.bdx.main_scene
    if first_scene == "":
        first_scene = current_scene.name

    ut.replace_line_containing(bdx_app, "scenes.add", '\t\tBdx.scenes.add(new Scene("'+first_scene+'"));')

    ut.remove_lines_containing(bdx_app, "Bdx.firstScene = ")
    ut.insert_lines_after(bdx_app, "scenes.add", ['\t\tBdx.firstScene = "'+first_scene+'";'])

    # - DesktopLauncher.java
    rx = str(current_scene.render.resolution_x)     # Because the main scene could be in another .blend,
    ry = str(current_scene.render.resolution_y)     # it's simplest to just use the current scene's render info

    dl = j(ut.src_root("desktop", "DesktopLauncher.java"), "DesktopLauncher.java")
    ut.set_file_var(dl, "title", '"'+ut.project_name()+'"')
    ut.set_file_var(dl, "width", rx)
    ut.set_file_var(dl, "height", ry)

    # - AndroidLauncher.java
    al = j(ut.src_root("android", "AndroidLauncher.java"), "AndroidLauncher.java")
    ut.set_file_var(al, "width", rx)
    ut.set_file_var(al, "height", ry)

    if runThread is not None and runThread.is_alive():

        f = open(j(ut.project_root(), "android", "assets", "finishedExport"), "w")
        f.close()

    if context.scene.bdx.post_export_program != "":
        cmd = context.scene.bdx.post_export_program
        old_cwd = os.getcwd()
        os.chdir(ut.project_root())
        subprocess.check_call(os.path.join(ut.project_root(), cmd))
        os.chdir(old_cwd)

    context.window.cursor_set("DEFAULT")

    export_time = time.time()

    if multiBlend and multipleBlendFilesExported:
        prev_auto_export = bpy.context.scene.bdx.auto_export
        bpy.context.scene.bdx.auto_export = False
        bpy.ops.wm.save_mainfile()                              # Save and reload to clear out orphan data left behind by
        bpy.ops.wm.open_mainfile(filepath=bpy.data.filepath)    # linked scenes
        bpy.context.scene.bdx.auto_export = prev_auto_export
    if not multiBlend:
        export_time = None

    if prof_scene_export:
        bpy.data.objects.remove(bpy.data.objects["__PBars"]);
        bpy.data.objects.remove(bpy.data.objects["__PBar"]);
        bpy.data.objects.remove(bpy.data.objects["__PCam"]);
        bpy.data.objects.remove(bpy.data.objects["__PDisplay"]);
        bpy.data.objects.remove(bpy.data.objects["__PBackground"]);
        bpy.data.objects.remove(bpy.data.objects["__PText"]);
        bpy.data.curves.remove(bpy.data.curves["__PText"]);
        bpy.data.meshes.remove(bpy.data.meshes["__PBar"]);
        bpy.data.meshes.remove(bpy.data.meshes["__PBackground"]);
        bpy.data.fonts.remove(bpy.data.fonts["BDXFontMono"]);

def run(self, context):

    global runThread

    if runThread is None or not runThread.is_alive():

        runThread = RunThread()
        runThread.daemon = True  # So exiting Blender will exit the game, too
        runThread.start()

class BdxExp(bpy.types.Operator):
    """Just exports scene data to .bdx files"""
    bl_idname = "object.bdxexp"
    bl_label = "Export BDX Data"

    def execute(self, context):

        export(self, context, bpy.context.scene.bdx.multi_blend_export, bpy.context.scene.bdx.diff_export)

        return {"FINISHED"}


class BdxRun(bpy.types.Operator):
    """Runs BDX using exported scene data"""
    bl_idname = "object.bdxrun"
    bl_label = "Run BDX"

    def execute(self, context):

        run(self, context)

        return {"FINISHED"}

class BdxExpRun(bpy.types.Operator):
    """Exports scene data and runs BDX"""
    bl_idname = "object.bdxexprun"
    bl_label = "Export and Run BDX"

    def execute(self, context):

        export(self, context, bpy.context.scene.bdx.multi_blend_export, bpy.context.scene.bdx.diff_export)

        run(self, context)

        return {"FINISHED"}


def register():
    bpy.utils.register_class(BdxExpRun)
    bpy.utils.register_class(BdxExp)
    bpy.utils.register_class(BdxRun)


def unregister():
    bpy.utils.unregister_class(BdxExpRun)
    bpy.utils.unregister_class(BdxExp)
    bpy.utils.unregister_class(BdxRun)
