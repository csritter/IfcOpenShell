# BlenderBIM Add-on - OpenBIM Blender Add-on
# Copyright (C) 2020, 2021 Dion Moult <dion@thinkmoult.com>
#
# This file is part of BlenderBIM Add-on.
#
# BlenderBIM Add-on is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# BlenderBIM Add-on is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with BlenderBIM Add-on.  If not, see <http://www.gnu.org/licenses/>.

import bpy
import copy
import math
import bmesh
import mathutils.geometry
import ifcopenshell
import ifcopenshell.util.type
import ifcopenshell.util.unit
import ifcopenshell.util.element
import blenderbim.bim.handler
import blenderbim.tool as tool
import blenderbim.core.type
import blenderbim.core.geometry
from blenderbim.bim.ifc import IfcStore
from math import pi, degrees, inf
from mathutils import Vector, Matrix
from ifcopenshell.api.pset.data import Data as PsetData
from ifcopenshell.api.material.data import Data as MaterialData
from blenderbim.bim.module.geometry.helper import Helper


def element_listener(element, obj):
    blenderbim.bim.handler.subscribe_to(obj, "mode", mode_callback)


def mode_callback(obj, data):
    for obj in set(bpy.context.selected_objects + [bpy.context.active_object]):
        if (
            not obj.data
            or not isinstance(obj.data, (bpy.types.Mesh, bpy.types.Curve, bpy.types.TextCurve))
            or not obj.BIMObjectProperties.ifc_definition_id
            or not bpy.context.scene.BIMProjectProperties.is_authoring
        ):
            return
        product = IfcStore.get_file().by_id(obj.BIMObjectProperties.ifc_definition_id)
        parametric = ifcopenshell.util.element.get_psets(product).get("EPset_Parametric")
        if not parametric or parametric["Engine"] != "BlenderBIM.DumbProfile":
            return
        if obj.mode == "EDIT":
            IfcStore.edited_objs.add(obj)
            bm = bmesh.from_edit_mesh(obj.data)
            bmesh.ops.dissolve_limit(bm, angle_limit=pi / 180 * 1, verts=bm.verts, edges=bm.edges)
            bmesh.update_edit_mesh(obj.data)
            bm.free()
        else:
            material_usage = ifcopenshell.util.element.get_material(product)
            x, y = obj.dimensions[0:2]
            if not material_usage.CardinalPoint:
                new_origin = obj.matrix_world @ (Vector(obj.bound_box[0]) + (Vector((x, y, 0)) / 2))
            elif material_usage.CardinalPoint == 1:
                new_origin = obj.matrix_world @ Vector(obj.bound_box[4])
            elif material_usage.CardinalPoint == 2:
                new_origin = obj.matrix_world @ (Vector(obj.bound_box[0]) + (Vector((x, 0, 0)) / 2))
            elif material_usage.CardinalPoint == 3:
                new_origin = obj.matrix_world @ Vector(obj.bound_box[0])
            elif material_usage.CardinalPoint == 4:
                new_origin = obj.matrix_world @ (Vector(obj.bound_box[4]) + (Vector((0, y, 0)) / 2))
            elif material_usage.CardinalPoint == 5:
                new_origin = obj.matrix_world @ (Vector(obj.bound_box[0]) + (Vector((x, y, 0)) / 2))
            elif material_usage.CardinalPoint == 6:
                new_origin = obj.matrix_world @ (Vector(obj.bound_box[0]) + (Vector((0, y, 0)) / 2))
            elif material_usage.CardinalPoint == 7:
                new_origin = obj.matrix_world @ Vector(obj.bound_box[7])
            elif material_usage.CardinalPoint == 8:
                new_origin = obj.matrix_world @ (Vector(obj.bound_box[3]) + (Vector((x, 0, 0)) / 2))
            elif material_usage.CardinalPoint == 9:
                new_origin = obj.matrix_world @ Vector(obj.bound_box[3])
            if (obj.matrix_world.translation - new_origin).length < 0.001:
                return
            obj.data.transform(
                Matrix.Translation(
                    (obj.matrix_world.inverted().to_quaternion() @ (obj.matrix_world.translation - new_origin))
                )
            )
            obj.matrix_world.translation = new_origin


class DumbProfileGenerator:
    def __init__(self, relating_type):
        self.relating_type = relating_type

    def generate(self):
        self.file = IfcStore.get_file()
        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(IfcStore.get_file())
        material = ifcopenshell.util.element.get_material(self.relating_type)
        if material and material.is_a("IfcMaterialProfileSet"):
            self.profile_set = material
        else:
            return

        self.body_context = ifcopenshell.util.representation.get_context(tool.Ifc.get(), "Model", "Body", "MODEL_VIEW")
        self.axis_context = ifcopenshell.util.representation.get_context(tool.Ifc.get(), "Model", "Axis", "GRAPH_VIEW")
        self.collection = bpy.context.view_layer.active_layer_collection.collection
        self.collection_obj = bpy.data.objects.get(self.collection.name)
        self.depth = 3
        self.rotation = 0
        self.location = Vector((0, 0, 0))
        return self.derive_from_cursor()

    def derive_from_cursor(self):
        self.location = bpy.context.scene.cursor.location
        return self.create_profile()

    def create_profile(self):
        ifc_classes = ifcopenshell.util.type.get_applicable_entities(self.relating_type.is_a(), self.file.schema)
        # Standard cases are deprecated, so let's cull them
        ifc_class = [c for c in ifc_classes if "StandardCase" not in c][0]

        mesh = bpy.data.meshes.new("Dummy")
        obj = bpy.data.objects.new(tool.Model.generate_occurrence_name(self.relating_type, ifc_class), mesh)
        obj.location = self.location
        if self.collection_obj and self.collection_obj.BIMObjectProperties.ifc_definition_id:
            obj.location[2] = self.collection_obj.location[2]
        self.collection.objects.link(obj)

        element = blenderbim.core.root.assign_class(
            tool.Ifc,
            tool.Collector,
            tool.Root,
            obj=obj,
            ifc_class=ifc_class,
            should_add_representation=False,
            context=self.body_context,
        )
        ifcopenshell.api.run("type.assign_type", self.file, related_object=element, relating_type=self.relating_type)
        if self.relating_type.is_a() in ["IfcBeamType", "IfcMemberType"]:
            obj.rotation_euler[0] = math.pi / 2
            obj.rotation_euler[2] = math.pi / 2

        if self.axis_context:
            representation = ifcopenshell.api.run(
                "geometry.add_axis_representation",
                tool.Ifc.get(),
                context=self.axis_context,
                axis=[(0.0, 0.0, 0.0), (0.0, 0.0, self.depth)],
            )
            ifcopenshell.api.run(
                "geometry.assign_representation", tool.Ifc.get(), product=element, representation=representation
            )

        representation = ifcopenshell.api.run(
            "geometry.add_profile_representation",
            tool.Ifc.get(),
            context=self.body_context,
            profile=self.profile_set.CompositeProfile or self.profile_set.MaterialProfiles[0].Profile,
            depth=self.depth,
        )
        ifcopenshell.api.run(
            "geometry.assign_representation", tool.Ifc.get(), product=element, representation=representation
        )
        blenderbim.core.geometry.switch_representation(
            tool.Geometry,
            obj=obj,
            representation=representation,
            should_reload=True,
            enable_dynamic_voids=False,
            is_global=True,
            should_sync_changes_first=False,
        )

        pset = ifcopenshell.api.run("pset.add_pset", self.file, product=element, name="EPset_Parametric")
        ifcopenshell.api.run("pset.edit_pset", self.file, pset=pset, properties={"Engine": "BlenderBIM.DumbProfile"})
        MaterialData.load(self.file)

        obj.select_set(True)

        return obj


class DumbProfileRegenerator:
    def regenerate_from_profile(self, usecase_path, ifc_file, settings):
        self.file = ifc_file
        profile = settings["profile"].Profile
        if not profile:
            return
        for element in self.get_elements_using_profile(profile):
            self.change_profile(element)

    def sync_object_from_profile(self, usecase_path, ifc_file, settings):
        self.file = ifc_file
        profile = settings["profile"].Profile
        if not profile:
            return
        for element in self.get_elements_using_profile(profile):
            self.sync_object(element)

    def get_elements_using_profile(self, profile):
        results = []
        for profile_set in [
            mp.ToMaterialProfileSet[0] for mp in self.file.get_inverse(profile) if mp.is_a("IfcMaterialProfile")
        ]:
            for inverse in self.file.get_inverse(profile_set):
                if not inverse.is_a("IfcMaterialProfileSetUsage"):
                    continue
                if self.file.schema == "IFC2X3":
                    for rel in self.file.get_inverse(inverse):
                        if not rel.is_a("IfcRelAssociatesMaterial"):
                            continue
                        results.extend(rel.RelatedObjects)
                else:
                    for rel in inverse.AssociatedTo:
                        results.extend(rel.RelatedObjects)
        return results

    def regenerate_from_type(self, usecase_path, ifc_file, settings):
        new_material = ifcopenshell.util.element.get_material(settings["relating_type"])
        if not new_material or not new_material.is_a("IfcMaterialProfileSet"):
            return
        self.change_profile(settings["related_object"])

    def change_profile(self, element):
        obj = IfcStore.get_element(element.id())
        if not obj:
            return
        representation = ifcopenshell.util.representation.get_representation(element, "Model", "Body", "MODEL_VIEW")
        if representation:
            blenderbim.core.geometry.switch_representation(
                tool.Geometry,
                obj=obj,
                representation=representation,
                should_reload=True,
                enable_dynamic_voids=True,
                is_global=True,
                should_sync_changes_first=False,
            )

    def sync_object(self, element):
        obj = IfcStore.get_element(element.id())
        if not obj or obj not in IfcStore.edited_objs:
            return
        bpy.ops.bim.update_representation(obj=obj.name)


class ExtendProfile(bpy.types.Operator):
    bl_idname = "bim.extend_profile"
    bl_label = "Extend Profile"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        selected_objs = context.selected_objects
        for obj in selected_objs:
            bpy.ops.bim.dynamically_void_product(obj=obj.name)
        if len(selected_objs) == 0:
            return {"FINISHED"}
        if not context.active_object:
            return {"FINISHED"}
        if len(selected_objs) == 1:
            DumbProfileExtender(None).join_E(context.active_object, context.scene.cursor.location)
            return {"FINISHED"}
        if len(selected_objs) < 2:
            return {"FINISHED"}
        for obj in selected_objs:
            if obj == context.active_object:
                continue
            DumbProfileExtender(None).join_T(obj, context.active_object)
        return {"FINISHED"}


class DumbProfileExtender:
    # A profile is a prismatic extrusion along its local Z axis.
    def __init__(self, profile, target=None, target_coordinate=None):
        self.profile = profile
        self.target = target
        self.target_coordinate = target_coordinate
        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        self.axis_context = ifcopenshell.util.representation.get_context(tool.Ifc.get(), "Model", "Axis", "GRAPH_VIEW")
        self.body_context = ifcopenshell.util.representation.get_context(tool.Ifc.get(), "Model", "Body", "MODEL_VIEW")

    def join_E(self, profile1, target):
        element1 = tool.Ifc.get_entity(profile1)
        if not element1:
            return
        axis1 = self.get_profile_axis(profile1)
        intersect, connection = mathutils.geometry.intersect_point_line(target, *axis1)
        connection = "ATEND" if connection > 0.5 else "ATSTART"

        ifcopenshell.api.run("geometry.disconnect_path", tool.Ifc.get(), element=element1, connection_type=connection)

        axis = copy.deepcopy(axis1)
        body = copy.deepcopy(axis1)
        axis[1 if connection == "ATEND" else 0] = intersect
        body[1 if connection == "ATEND" else 0] = intersect
        self.recreate_profile(element1, profile1, axis, body)

    def join_T(self, profile1, profile2):
        element1 = tool.Ifc.get_entity(profile1)
        element2 = tool.Ifc.get_entity(profile2)
        axis1 = self.get_profile_axis(profile1)
        axis2 = self.get_profile_axis(profile2)
        intersect = tool.Cad.intersect_edges(axis1, axis2)
        if intersect:
            intersect, _ = intersect
        else:
            return
        connection = "ATEND" if tool.Cad.edge_percent(intersect, axis1) > 0.5 else "ATSTART"

        ifcopenshell.api.run(
            "geometry.connect_path",
            tool.Ifc.get(),
            related_element=element1,
            relating_element=element2,
            relating_connection="ATPATH",
            related_connection=connection,
        )

        self.recreate_profile(element1, profile1, axis1, axis1)

    def recreate_profile(self, element, obj, axis=None, body=None):
        if axis is None or body is None:
            axis = body = self.get_profile_axis(obj)
        self.axis = copy.deepcopy(axis)
        self.body = copy.deepcopy(body)
        material = ifcopenshell.util.element.get_material(element, should_skip_usage=True)
        self.profile = material.CompositeProfile or material.MaterialProfiles[0].Profile
        height = self.get_height(tool.Ifc.get().by_id(obj.data.BIMMeshProperties.ifc_definition_id))
        self.clippings = []

        for rel in element.ConnectedTo:
            connection = rel.RelatingConnectionType
            other = tool.Ifc.get_object(rel.RelatedElement)
            if connection not in ["ATPATH", "NOTDEFINED"]:
                self.join(obj, other, connection, is_relating=True)
        for rel in element.ConnectedFrom:
            connection = rel.RelatedConnectionType
            other = tool.Ifc.get_object(rel.RelatingElement)
            if connection not in ["ATPATH", "NOTDEFINED"]:
                self.join(obj, other, connection, is_relating=False)

        new_matrix = copy.deepcopy(obj.matrix_world)
        new_matrix.col[3] = self.body[0].to_4d()
        new_matrix.invert()
        self.clippings = [new_matrix @ c for c in self.clippings]

        depth = (self.body[1] - self.body[0]).length

        if self.axis_context:
            axis = [(new_matrix @ a) for a in self.axis]
            new_axis = ifcopenshell.api.run(
                "geometry.add_axis_representation", tool.Ifc.get(), context=self.axis_context, axis=axis
            )
            old_axis = ifcopenshell.util.representation.get_representation(element, "Model", "Axis", "GRAPH_VIEW")
            if old_axis:
                for inverse in tool.Ifc.get().get_inverse(old_axis):
                    ifcopenshell.util.element.replace_attribute(inverse, old_axis, new_axis)
                blenderbim.core.geometry.remove_representation(
                    tool.Ifc, tool.Geometry, obj=obj, representation=old_axis
                )
            else:
                ifcopenshell.api.run(
                    "geometry.assign_representation", tool.Ifc.get(), product=element, representation=new_axis
                )

        new_body = ifcopenshell.api.run(
            "geometry.add_profile_representation",
            tool.Ifc.get(),
            context=self.body_context,
            profile=self.profile,
            depth=depth,
            clippings=self.clippings,
        )

        old_body = ifcopenshell.util.representation.get_representation(element, "Model", "Body", "MODEL_VIEW")
        if old_body:
            for inverse in tool.Ifc.get().get_inverse(old_body):
                ifcopenshell.util.element.replace_attribute(inverse, old_body, new_body)
            obj.data.BIMMeshProperties.ifc_definition_id = int(new_body.id())
            obj.data.name = f"{self.body_context.id()}/{new_body.id()}"
            blenderbim.core.geometry.remove_representation(tool.Ifc, tool.Geometry, obj=obj, representation=old_body)
        else:
            ifcopenshell.api.run(
                "geometry.assign_representation", tool.Ifc.get(), product=element, representation=new_body
            )

        obj.location[0], obj.location[1], obj.location[2] = self.body[0]
        bpy.context.view_layer.update()
        if tool.Ifc.is_moved(obj):
            blenderbim.core.geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)
        blenderbim.core.geometry.switch_representation(
            tool.Geometry,
            obj=obj,
            representation=new_body,
            should_reload=True,
            enable_dynamic_voids=False,
            is_global=True,
            should_sync_changes_first=False,
        )

    def join(self, profile1, profile2, connection, is_relating=True):
        element1 = tool.Ifc.get_entity(profile1)
        element2 = tool.Ifc.get_entity(profile2)
        axis1 = self.get_profile_axis(profile1)
        axis2 = self.get_profile_axis(profile2)

        angle = tool.Cad.angle_edges(axis1, axis2, signed=False, degrees=True)
        if tool.Cad.is_x(angle, (0, 180)):
            return False

        intersect, _ = tool.Cad.intersect_edges(axis1, axis2)

        proposed_axis = [self.axis[0], intersect] if connection == "ATEND" else [intersect, self.axis[1]]

        if tool.Cad.is_x(tool.Cad.angle_edges(self.axis, proposed_axis, degrees=True), 180):
            # The user has moved the wall into an invalid position that cannot connect at the desired end
            return False

        self.axis[1 if connection == "ATEND" else 0] = intersect

        # Work out body extents

        if connection == "ATEND":
            axisl = ((profile2.matrix_world.inverted() @ axis1[1]) - (profile2.matrix_world.inverted() @ axis1[0]))
        elif connection == "ATSTART":
            axisl = ((profile2.matrix_world.inverted() @ axis1[0]) - (profile2.matrix_world.inverted() @ axis1[1]))
        xy_angle = math.degrees(Vector((1, 0)).angle_signed(axisl.normalized().to_2d()))
        if xy_angle >= -135 and xy_angle <= -45:
            closest_plane = "bottom"
            furthest_plane = "top"
        elif xy_angle >= 45 and xy_angle <= 135:
            closest_plane = "top"
            furthest_plane = "bottom"
        elif xy_angle >= -45 and xy_angle <= 45:
            closest_plane = "left"
            furthest_plane = "right"
        else:
            closest_plane = "right"
            furthest_plane = "left"

        is_orthogonal = tool.Cad.is_x(tool.Cad.angle_edges(axis1, axis2, degrees=True), 90, tolerance=0.001)

        if connection == "ATEND":
            if tool.Cad.is_x(abs(xy_angle), (0, 90, 180), tolerance=0.001) and is_orthogonal:
                plane = self.get_profile_plane(profile2, furthest_plane if is_relating else closest_plane)
                intersect = mathutils.geometry.intersect_line_plane(*axis1, plane.col[3].to_3d(), plane.col[2].to_3d())
                self.body[1] = intersect
            else:
                plane = self.get_profile_plane(profile2, furthest_plane if is_relating else closest_plane)
                intersect = mathutils.geometry.intersect_line_plane(*axis1, plane.col[3].to_3d(), plane.col[2].to_3d())
                max_dim = self.get_max_bound_box_dimension(profile1)
                self.body[1] = intersect + profile1.matrix_world.to_quaternion() @ Vector((0, 0, max_dim))
                self.clippings.append(plane)
        elif connection == "ATSTART":
            if tool.Cad.is_x(abs(xy_angle), (0, 90, 180), tolerance=0.001) and is_orthogonal:
                plane = self.get_profile_plane(profile2, furthest_plane if is_relating else closest_plane)
                intersect = mathutils.geometry.intersect_line_plane(*axis1, plane.col[3].to_3d(), plane.col[2].to_3d())
                self.body[0] = intersect
            else:
                plane = self.get_profile_plane(profile2, furthest_plane if is_relating else closest_plane)
                intersect = mathutils.geometry.intersect_line_plane(*axis1, plane.col[3].to_3d(), plane.col[2].to_3d())
                max_dim = self.get_max_bound_box_dimension(profile1)
                self.body[0] = intersect - profile1.matrix_world.to_quaternion() @ Vector((0, 0, max_dim))
                self.clippings.append(plane)

    def get_max_bound_box_dimension(self, obj):
        x = [v[0] for v in obj.bound_box]
        y = [v[1] for v in obj.bound_box]
        x_dim = max(x) - min(x)
        y_dim = max(y) - min(y)
        return x_dim if x_dim > y_dim else y_dim

    def get_profile_plane(self, obj, plane):
        # Get a matrix for one of the bounding planes of the profile to be used as cutting plane
        # Here's an example of looking at the end of an I-Beam profile with a +Z extrusion out of the screen
        #        top
        #      +-----+           Y
        #      |=====|           ^
        #      |  |  |           |
        # left |  |  | right     Z->X
        #      |  |  |
        #      |=====|
        #      +-----+
        #      bottom
        if plane == "top":
            max_y = max([v[1] for v in obj.bound_box])
            p = obj.matrix_world @ Vector((0, max_y, 0))
            x_axis = obj.matrix_world.to_quaternion() @ Vector((0, 0, 1))
            y_axis = obj.matrix_world.to_quaternion() @ Vector((-1, 0, 0))
            z_axis = obj.matrix_world.to_quaternion() @ Vector((0, -1, 0))
        elif plane == "bottom":
            min_y = min([v[1] for v in obj.bound_box])
            p = obj.matrix_world @ Vector((0, min_y, 0))
            x_axis = obj.matrix_world.to_quaternion() @ Vector((0, 0, 1))
            y_axis = obj.matrix_world.to_quaternion() @ Vector((1, 0, 0))
            z_axis = obj.matrix_world.to_quaternion() @ Vector((0, 1, 0))
        elif plane == "right":
            max_x = max([v[0] for v in obj.bound_box])
            p = obj.matrix_world @ Vector((max_x, 0, 0))
            x_axis = obj.matrix_world.to_quaternion() @ Vector((0, 0, 1))
            y_axis = obj.matrix_world.to_quaternion() @ Vector((0, 1, 0))
            z_axis = obj.matrix_world.to_quaternion() @ Vector((-1, 0, 0))
        elif plane == "left":
            min_x = min([v[0] for v in obj.bound_box])
            p = obj.matrix_world @ Vector((min_x, 0, 0))
            x_axis = obj.matrix_world.to_quaternion() @ Vector((0, 0, 1))
            y_axis = obj.matrix_world.to_quaternion() @ Vector((0, -1, 0))
            z_axis = obj.matrix_world.to_quaternion() @ Vector((1, 0, 0))
        return self.create_matrix(p, x_axis, y_axis, z_axis)

    def create_matrix(self, p, x, y, z):
        return Matrix(
            (
                (x[0], y[0], z[0], p[0]),
                (x[1], y[1], z[1], p[1]),
                (x[2], y[2], z[2], p[2]),
                (0.0, 0.0, 0.0, 1.0),
            )
        )

    def get_profile_axis(self, obj):
        z_values = [v[2] for v in obj.bound_box]
        return [
            (obj.matrix_world @ Vector((0.0, 0.0, min(z_values)))),
            (obj.matrix_world @ Vector((0.0, 0.0, max(z_values)))),
        ]

    # TODO
    def get_height(self, representation):
        height = 3.0
        item = representation.Items[0]
        while True:
            if item.is_a("IfcExtrudedAreaSolid"):
                height = item.Depth * self.unit_scale
                break
            elif item.is_a("IfcBooleanClippingResult"):
                item = item.FirstOperand
            else:
                break
        return height


    # An extension of a profile is determined by casting a ray from the cardinal
    # point of either extreme along the profiles local Z axis to a target
    # object. The nearest result from this raycast will be the target extension
    # point.
    def extend_old(self):
        extension_data = self.get_closest_extension_point()
        if not extension_data:
            return
        if extension_data["end"] == "bottom":
            self.profile.matrix_world.translation = extension_data["contact"]
            if extension_data["direction"] == "up":
                self.shift_top_faces(-extension_data["distance"])
            elif extension_data["direction"] == "down":
                self.shift_top_faces(extension_data["distance"])
        if extension_data["end"] == "top":
            if extension_data["direction"] == "up":
                self.shift_top_faces(extension_data["distance"])
            elif extension_data["direction"] == "down":
                self.shift_top_faces(-extension_data["distance"])

    def shift_top_faces(self, z):
        vertices = []
        for f in self.get_profile_top_faces():
            vertices.extend(f.vertices)
        for v in set(vertices):
            self.profile.data.vertices[v].co.z += z

    # An top face is defined as having at least one vertex on the max
    # Z-axis, and a non-insignificant Z component of its face normal
    def get_profile_top_faces(self):
        faces = []
        max_z = max([v[2] for v in self.profile.bound_box])
        for f in self.profile.data.polygons:
            if abs(f.normal.z) < 0.1:
                continue
            for v in f.vertices:
                if self.profile.data.vertices[v].co.z == max_z:
                    faces.append(f)
                    break
        return faces

    def get_closest_extension_point(self):
        top = self.profile.matrix_world @ Vector((0, 0, self.profile.dimensions[2]))
        bottom = self.profile.matrix_world.translation
        up = self.profile.matrix_world.to_quaternion() @ Vector((0, 0, 1))
        down = self.profile.matrix_world.to_quaternion() @ Vector((0, 0, -1))

        if self.target:
            return self.get_closest_extension_point_from_target_obj(top, bottom, up, down)
        elif self.target_coordinate:
            return self.get_closest_extension_point_from_target_coordinate(top, bottom, up, down)

    def get_closest_extension_point_from_target_coordinate(self, top, bottom, up, down):
        point = mathutils.geometry.intersect_line_plane(
            bottom, top, self.target_coordinate, self.profile.matrix_world.to_quaternion() @ Vector((0, 0, 1))
        )
        if not point:
            return
        top_distance = (point - top).length
        bottom_distance = (point - bottom).length
        if top_distance < bottom_distance:
            intersection_point, signed_distance = mathutils.geometry.intersect_point_line(point, top, bottom)
            if signed_distance > 0:
                return {"contact": point, "distance": top_distance, "end": "top", "direction": "down"}
            else:
                return {"contact": point, "distance": top_distance, "end": "top", "direction": "up"}
        else:
            intersection_point, signed_distance = mathutils.geometry.intersect_point_line(point, bottom, top)
            if signed_distance > 0:
                return {"contact": point, "distance": bottom_distance, "end": "bottom", "direction": "up"}
            else:
                return {"contact": point, "distance": bottom_distance, "end": "bottom", "direction": "down"}

    def get_closest_extension_point_from_target_obj(self, top, bottom, up, down):
        t_top = self.target.matrix_world.inverted() @ top
        t_bottom = self.target.matrix_world.inverted() @ bottom
        t_up = self.target.matrix_world.inverted().to_quaternion() @ up
        t_down = self.target.matrix_world.inverted().to_quaternion() @ down

        results = []
        results.append((self.target.ray_cast(t_top, t_up, distance=50), top, "top", "up"))
        results.append((self.target.ray_cast(t_top, t_down, distance=50), top, "top", "down"))
        results.append((self.target.ray_cast(t_bottom, t_up, distance=50), bottom, "bottom", "up"))
        results.append((self.target.ray_cast(t_bottom, t_down, distance=50), bottom, "bottom", "down"))

        min_distance = float(inf)
        final = None

        for result in results:
            if result[0][0]:
                contact = self.target.matrix_world @ result[0][1]
                distance = (contact - result[1]).length
                if distance < min_distance:
                    min_distance = distance
                    final = {"contact": contact, "distance": distance, "end": result[2], "direction": result[3]}

        return final
