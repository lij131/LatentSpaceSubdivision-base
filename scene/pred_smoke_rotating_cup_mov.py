import argparse
import os
from tqdm import trange

from timeit import default_timer as timer

import numpy as np
from collections import deque
from perlin import TileableNoise
from math import sin, pi
from random import seed, random 

try:
	from manta import *
	import gc
except ImportError:
	pass

import sys
sys.path.append(sys.path[0]+"/../")

from scene_storage import *
from keras_data import read_args_file

parser = argparse.ArgumentParser()
parser.add_argument("--load_path", type=str, required=True)
parser.add_argument('--warmup_steps', type=int, default=10)
parser.add_argument('--seed', type=int, default=10)
parser.add_argument('--num_frames', type=int, default=100)
parser.add_argument('--num_scenes', type=int, default=1)
parser.add_argument('--output_images', action='store_true')
parser.add_argument('--dont_delete_images', action='store_true')
parser.add_argument('--output_uni', action='store_true')
parser.add_argument('--obsRotationMaxScale', type=float, default=1.0) # only use for testing the border cases
parser.add_argument('--show_gui', action='store_true')
parser.add_argument('--classic_ae', action='store_true')
parser.add_argument('--upres', action='store_true')
parser.add_argument('--profile', action='store_true')
add_storage_args(parser)

pred_args = parser.parse_args()

# Prepare directories
pred_config = prepare_prediction_directory(pred_args, "pred_smoke_rotating_cup_mov")

# Load norm factors
norm_factors = {
	"normalization_factor_v": load_range(os.path.join(pred_config.net_config.data_path, "v_range.txt")),
	"normalization_factor_d": load_range(os.path.join(pred_config.net_config.data_path, "d_range.txt"))
}

# Load networks
net = initialize_networks(pred_args, pred_config.net_config, norm_factors)

# Load dataset args
args = DictToNamespace(net.dataset_meta_info)
args.show_gui = pred_args.show_gui

def meshRotationLimit(interpolant, max_rotation):
	return interpolant * max_rotation * pi

# Setup random
noise = TileableNoise(seed=pred_args.seed)
np.random.seed(seed=pred_args.seed)
seed(pred_args.seed)

assert net.sup_param_count == 2, "Supervised param count {} does not match {}!".format(net.sup_param_count, 2)

def main():
	warnings = []

	# create solver
	m = initialize_manta(args)
	prepare_additional_fields_manta(m, pred_args)

	buoyancy = vec3(0, float(args.buoyancy), 0)
	radius = m.gs.x * float(args.smoke_radius)

	v_ = np.zeros([m.res_z, m.res_y, m.res_x, 3], dtype=np.float32)
	d_ = np.zeros([m.res_z, m.res_y, m.res_x, 1], dtype=np.float32)

	print("prepare mesh")
	meshIndex = 0
	meshSigma = 1.5
	meshSize = float(args.obstacle_size)
	meshScale = vec3(m.gs.x*meshSize)
	meshScale.y *= 0.9
	meshfile = "meshes/cup.obj"
	mesh = []
	mesh.append(m.s.create(Mesh))
	mesh[-1].load( meshfile )
	mesh.append(m.s.create(Mesh))
	mesh[-1].load( meshfile )

	print('start generation')

	# pre-generate noise, so that all generated scenes for prediction and simulation look the same
	n_rot_list = [] # indexing: cur_scene_id * pred_args.num_frames + cur_frame
	n_pos_list = [] # indexing: cur_scene_id * pred_args.num_frames + cur_frame
	obs_rot_max_list = [] # indexing: cur_scene_id
	for i in range(pred_args.num_scenes):
		# noise
		noise.randomize()
		nx_ = noise.rng.randint(200)*float(args.nscale)
		ny_ = noise.rng.randint(200)*float(args.nscale)
		nz_ = noise.rng.randint(200)*float(args.nscale)

		# max rotation
		obsRotationMax_ = random() * float(args.max_obstacle_rot) * pred_args.obsRotationMaxScale
		obs_rot_max_list.append(obsRotationMax_)

		for t in range(pred_args.num_frames):
			# Rotation
			nx_rot = noise.noise3(x=t*float(args.nscale), y=ny_, z=nz_, repeat=int(args.nrepeat))
			# limit to [-obsRotationMax*pi, obsRotationMax*pi]
			curRotAngle = meshRotationLimit(nx_rot, obsRotationMax_)
			if curRotAngle / pi > float(args.max_obstacle_rot) or curRotAngle / pi < -float(args.max_obstacle_rot):
				warnings.append("nqx_rot[-1] {} not in range [{},{}]".format(curRotAngle / pi, -float(args.max_obstacle_rot), float(args.max_obstacle_rot)))
			n_rot_list.append(curRotAngle / pi)

			# Position
			nz_pos = noise.noise3(x=nx_, y=ny_, z=t*float(args.nscale), repeat=int(args.nrepeat))
			pz_pos = (nz_pos+1)*0.5 * (float(args.max_src_pos)-float(args.min_src_pos)) + float(args.min_src_pos) # [minx, maxx]
			if pz_pos > float(args.max_src_pos) or pz_pos < float(args.min_src_pos):
				warnings.append("pz_pos {} not in range [{},{}]".format(pz_pos, float(args.min_src_pos), float(args.max_src_pos)))
			n_pos_list.append(pz_pos)

	per_scene_duration = []
	per_scene_advection_duration = []
	per_scene_solve_duration = []

	# Start simulation
	for i in range(pred_args.num_scenes):
		m.flags.initDomain(boundaryWidth=int(args.bWidth))
		m.flags.fillGrid()
		setOpenBound(m.flags, int(args.bWidth), args.open_bound, FlagOutflow|FlagEmpty)

		m.vel.clear()
		m.density.clear()
		m.pressure.clear()
		m.obsVel.clear()
		m.phiObs.clear()
		
		# init obstacle properties
		initial_pz_pos = n_pos_list[i * pred_args.num_frames + 0]
		obsPos = vec3(initial_pz_pos, float(args.obstacle_pos_y), 0.5)
		m.obsVel.setConst(vec3(0,0,0))
		m.obsVel.setBound(value=Vec3(0.), boundaryWidth=int(args.bWidth)+1) # make sure walls are static
		
		curRotAngle = n_rot_list[i * pred_args.num_frames + 0] * pi

		mesh[0].load( meshfile )
		mesh[0].scale( meshScale )
		mesh[0].rotate( vec3(0.0,0.0,1.0)  * curRotAngle )
		mesh[0].offset( m.gs*obsPos )

		mesh[1].load( meshfile )
		mesh[1].scale( meshScale )
		mesh[1].rotate( vec3(0.0,0.0,1.0)  * curRotAngle )
		mesh[1].offset( m.gs*obsPos )

		# create source
		source = m.s.create(Sphere, center=m.gs*vec3(obsPos.x, float(args.smoke_pos_y), 0.5), radius=radius)

		# param_ stores history from beginning of scene to current frame
		param_ = [n_rot_list[i * pred_args.num_frames : i * pred_args.num_frames + 0], n_pos_list[i * pred_args.num_frames : i * pred_args.num_frames + 0]]

		# print settings
		print("Obs Pos: {}".format(obsPos))
		print("Obs Rot Max: {}".format(obs_rot_max_list[i]))
		print("Smoke Pos: {}".format(m.gs*vec3(obsPos.x, float(args.smoke_pos_y), 0.5)))
		print("Smoke Radius: {}".format(radius))

		per_frame_advection_duration = []
		per_frame_solve_duration = []

		for t in range( pred_args.num_frames ):
			start = timer()

			if not pred_args.profile:
				print("Frame {}".format(t), end="\r")

			# Supervised Params -> rotation, position
			curRotAngle = n_rot_list[i * pred_args.num_frames + t] * pi
			pz_pos		= n_pos_list[i * pred_args.num_frames + t]
			# param_ stores history from beginning of scene to current frame
			param_ = [n_rot_list[i * pred_args.num_frames : i * pred_args.num_frames + t], n_pos_list[i * pred_args.num_frames : i * pred_args.num_frames + t]]

			# Update obsPos
			obsPos = vec3(pz_pos, float(args.obstacle_pos_y), 0.5)

			# Apply Inflow
			source = m.s.create(Sphere, center=m.gs * vec3(obsPos.x, float(args.smoke_pos_y), 0.5), radius=radius)

			# Apply inflow
			source.applyToGrid(grid=m.density, value=1)

			# Simulation Loop
			m.obsVel.setConst(Vec3(0.))
			m.obsVel.setBound(value=Vec3(0.), boundaryWidth=int(args.bWidth)+1) # make sure walls are static

			advectSemiLagrange(flags=m.flags, vel=m.vel, grid=m.density, order=1) 
			advectSemiLagrange(flags=m.flags, vel=m.vel, grid=m.vel,     order=2)
			resetOutflow(flags=m.flags, real=m.density) 

			m.flags.initDomain(boundaryWidth=int(args.bWidth)) 
			m.flags.fillGrid()
			setOpenBound(m.flags, int(args.bWidth), args.open_bound, FlagOutflow|FlagEmpty) # yY == floor and top

			# reset obstacle levelset
			m.phiObs.clear()

			end = timer()
			if t > pred_args.warmup_steps:
				per_frame_advection_duration.append(end-start)

			# move mesh
			oldMeshIndex = (meshIndex+1) % len(mesh)
			mesh[meshIndex].load( meshfile )
			mesh[meshIndex].scale( meshScale )
			mesh[meshIndex].rotate( vec3(0.0,0.0,1.0) * curRotAngle )
			mesh[meshIndex].offset( m.gs*obsPos )

			# compute velocity for "old" meshIndex (from old to new position)
			mesh[meshIndex].computeVelocity(mesh[oldMeshIndex], m.obsVel)
			m.obsVel.setBound(value=Vec3(0.), boundaryWidth=int(args.bWidth)+1) # make sure walls are static

			mesh[oldMeshIndex].computeLevelset(m.phiObs, meshSigma)

			# advance index
			meshIndex += 1
			meshIndex %= len(mesh)

			setObstacleFlags(flags=m.flags, phiObs=m.phiObs) 
			m.flags.fillGrid()
			# clear smoke inside
			mesh[meshIndex].applyMeshToGrid(grid=m.density, value=0., meshSigma=meshSigma)

			setWallBcs(flags=m.flags, vel=m.vel, phiObs=m.phiObs, obvel=m.obsVel)

			# Shift supervised parameters from individual [min, max] to [-1,1]
			norm_curRotAngle =  ((curRotAngle / pi) - float(args.min_obstacle_rot)) / (float(args.max_obstacle_rot) - float(args.min_obstacle_rot)) * 2.0 - 1.0
			norm_pz_pos =  (pz_pos - float(args.min_src_pos)) / (float(args.max_src_pos) - float(args.min_src_pos)) * 2.0 - 1.0

			start = timer()

			# Solve or Prediction
			if t < pred_args.warmup_steps or pred_args.prediction_type == "simulation" or pred_args.prediction_type == "enc_dec":
				addBuoyancy(density=m.density, vel=m.vel, gravity=buoyancy, flags=m.flags)
				solvePressure(flags=m.flags, vel=m.vel, pressure=m.pressure)

				# "Fix" velocity values inside of obstacle to support stream functions
				extrapolateMACSimple(flags=m.flags, vel=m.obsVel, distance=3)
				copyMACData(m.obsVel, m.vel, m.flags, CellType_TypeObstacle, int(args.bWidth))

				if not pred_args.prediction_type == "simulation":
					copyGridToArrayMAC(target=v_, source=m.vel)
					copyGridToArrayReal(target=d_, source=m.density)

					# Encode
					enc = encode(v_, d_, net, m, pred_config.net_config)

					# Set supervised parameters -> (batch, dim)
					enc[0, -2] = norm_curRotAngle
					enc[0, -1] = norm_pz_pos
					if net.classic_ae:
						enc[0, net.rec_pred.z_num_vel-2] = norm_curRotAngle
						enc[0, net.rec_pred.z_num_vel-1] = norm_pz_pos
					net.prediction_history.add_simulation(enc[0])

					if t >= pred_args.warmup_steps and pred_args.prediction_type == "enc_dec":
						decode(enc, net, m, pred_config.net_config, pred_args.prediction_type)
			else:
				# ~~ Start of Prediction
				if pred_args.prediction_type == "vel_prediction" and "density" in pred_config.net_config.data_type:
					# overwrite density part of history with current density
					# 1) encode current density d0, use "old" velocity field
					# not divergence free... if copied now to v_
					# copyGridToArrayMAC(target=v_, source=m.vel)
					#copyGridToArrayMAC(target=v_, source=m.vel)
					copyGridToArrayReal(target=d_, source=m.density)
					# encode current density
					encode_density(v_, d_, net, m)

				# predict next frame
				cur_pred = predict_ls(net)

				# supervised entries
				cur_pred[0,-1,-2] = norm_curRotAngle
				cur_pred[0,-1,-1] = norm_pz_pos
				if net.classic_ae:
					cur_pred[0, -1, net.rec_pred.z_num_vel-2] = norm_curRotAngle
					cur_pred[0, -1, net.rec_pred.z_num_vel-1] = norm_pz_pos

				# add to history
				net.prediction_history.add_prediction(cur_pred[0])

				# decode (ae)
				decode(cur_pred[0], net, m, pred_config.net_config, pred_args.prediction_type)
				# ~~ End of Prediction

			copyGridToArrayMAC(target=v_, source=m.vel)
			if not pred_args.profile:
				# Store to disk
				copyGridToArrayReal(target=d_, source=m.density)
				if net.is_3d and pred_args.output_uni:
					store_density_blender(m.density_upres if pred_args.upres else m.density, pred_config.log_dir % i, t, density_blender=m.density_blender, density_blender_cubic=m.density_blender_cubic)

				store_velocity(v_, pred_config.log_dir % i, t, param_, pred_args.field_path_format)
				store_density(d_, pred_config.log_dir % i, t, param_, pred_args.field_path_format)

			end = timer()
			if t > pred_args.warmup_steps:
				per_frame_solve_duration.append(end-start)

			m.s.step()

			if not pred_args.profile and pred_args.output_images:
				screenshot(m.gui, pred_config.log_dir % i, t, density=m.density, scale=2.0)

		if not pred_args.profile and pred_args.output_images:
			convert_sequence( os.path.join(pred_config.log_dir % i, 'screenshots'), output_name="%06d" % i, file_format="%06d.jpg" if m.gui else "%06d.ppm", delete_images=not pred_args.dont_delete_images )

		per_scene_advection_duration.append(np.array(per_frame_advection_duration))
		per_scene_solve_duration.append(np.array(per_frame_solve_duration))
		per_scene_duration.append(np.array(per_frame_advection_duration) + np.array(per_frame_solve_duration))

		gc.collect()

	store_profile_info(pred_config, per_scene_duration, per_scene_advection_duration, per_scene_solve_duration)

	if len(warnings) > 0:
		print("Warnings")
		for w in warnings:
			print("\t"+w)
		print("Done with warnings!")
	else:
		print("Done")

if __name__ == '__main__':
	main()
