#!/usr/bin/env python3
#
# Copyright (C) 2021 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections import namedtuple
import google.protobuf
from typing import List, Optional

from binary_cache_builder import BinaryCacheBuilder
from pprof_proto_generator import load_pprof_profile, PprofProfileGenerator
from . test_utils import TestBase, TestHelper


class TestPprofProtoGenerator(TestBase):
    def run_generator(self, options=None, testdata_file='perf_with_interpreter_frames.data'):
        testdata_path = TestHelper.testdata_path(testdata_file)
        options = options or []
        self.run_cmd(['pprof_proto_generator.py', '-i', testdata_path] + options)
        return self.run_cmd(['pprof_proto_generator.py', '--show'], return_output=True)

    def generate_profile(self, options: Optional[List[str]], testdata_files: List[str]):
        testdata_paths = [TestHelper.testdata_path(f) for f in testdata_files]
        options = options or []
        self.run_cmd(['pprof_proto_generator.py', '-i'] + testdata_paths + options)
        return load_pprof_profile('pprof.profile')

    def test_show_art_frames(self):
        art_frame_str = 'art::interpreter::DoCall'
        # By default, don't show art frames.
        self.assertNotIn(art_frame_str, self.run_generator())
        # Use --show_art_frames to show art frames.
        self.assertIn(art_frame_str, self.run_generator(['--show_art_frames']))

    def test_pid_filter(self):
        key = 'PlayScene::DoFrame()'  # function in process 10419
        self.assertIn(key, self.run_generator())
        self.assertIn(key, self.run_generator(['--pid', '10419']))
        self.assertIn(key, self.run_generator(['--pid', '10419', '10416']))
        self.assertNotIn(key, self.run_generator(['--pid', '10416']))

    def test_tid_filter(self):
        key1 = 'art::ProfileSaver::Run()'  # function in thread 10459
        key2 = 'PlayScene::DoFrame()'  # function in thread 10463
        for options in ([], ['--tid', '10459', '10463']):
            output = self.run_generator(options)
            self.assertIn(key1, output)
            self.assertIn(key2, output)
        output = self.run_generator(['--tid', '10459'])
        self.assertIn(key1, output)
        self.assertNotIn(key2, output)
        output = self.run_generator(['--tid', '10463'])
        self.assertNotIn(key1, output)
        self.assertIn(key2, output)

    def test_comm_filter(self):
        key1 = 'art::ProfileSaver::Run()'  # function in thread 'Profile Saver'
        key2 = 'PlayScene::DoFrame()'  # function in thread 'e.sample.tunnel'
        for options in ([], ['--comm', 'Profile Saver', 'e.sample.tunnel']):
            output = self.run_generator(options)
            self.assertIn(key1, output)
            self.assertIn(key2, output)
        output = self.run_generator(['--comm', 'Profile Saver'])
        self.assertIn(key1, output)
        self.assertNotIn(key2, output)
        output = self.run_generator(['--comm', 'e.sample.tunnel'])
        self.assertNotIn(key1, output)
        self.assertIn(key2, output)

    def test_build_id(self):
        """ Test the build ids generated are not padded with zeros. """
        self.assertIn('build_id: e3e938cc9e40de2cfe1a5ac7595897de(', self.run_generator())

    def test_location_address(self):
        """ Test if the address of a location is within the memory range of the corresponding
            mapping.
        """
        profile = self.generate_profile(None, ['perf_with_interpreter_frames.data'])
        # pylint: disable=no-member
        for location in profile.location:
            mapping = profile.mapping[location.mapping_id - 1]
            self.assertLessEqual(mapping.memory_start, location.address)
            self.assertGreaterEqual(mapping.memory_limit, location.address)

    def test_multiple_perf_data(self):
        """ Test reporting multiple recording file. """
        profile1 = self.generate_profile(None, ['aggregatable_perf1.data'])
        profile2 = self.generate_profile(None, ['aggregatable_perf2.data'])
        profile_both = self.generate_profile(
            None, ['aggregatable_perf1.data', 'aggregatable_perf2.data'])
        # pylint: disable=no-member
        self.assertGreater(len(profile_both.sample), len(profile1.sample))
        self.assertGreater(len(profile_both.sample), len(profile2.sample))

    def test_proguard_mapping_file(self):
        """ Test --proguard-mapping-file option. """
        testdata_file = 'perf_need_proguard_mapping.data'
        proguard_mapping_file = TestHelper.testdata_path('proguard_mapping.txt')
        original_methodname = 'androidx.fragment.app.FragmentActivity.startActivityForResult'
        # Can't show original method name without proguard mapping file.
        self.assertNotIn(original_methodname, self.run_generator(testdata_file=testdata_file))
        # Show original method name with proguard mapping file.
        self.assertIn(original_methodname, self.run_generator(
            ['--proguard-mapping-file', proguard_mapping_file], testdata_file))

    def test_use_binary_cache(self):
        testdata_file = TestHelper.testdata_path('runtest_two_functions_arm64_perf.data')

        # Build binary_cache.
        binary_cache_builder = BinaryCacheBuilder(TestHelper.ndk_path, False)
        binary_cache_builder.build_binary_cache(testdata_file, [TestHelper.testdata_dir])

        # Generate profile.
        output = self.run_generator(testdata_file=testdata_file)
        self.assertIn('simpleperf_runtest_two_functions_arm64', output)
        self.assertIn('two_functions.cpp', output)

    def test_line_info(self):
        """ Check line numbers generated in profile. """
        testdata_file = TestHelper.testdata_path('runtest_two_functions_arm64_perf.data')

        # Build binary_cache.
        binary_cache_builder = BinaryCacheBuilder(TestHelper.ndk_path, False)
        binary_cache_builder.build_binary_cache(testdata_file, [TestHelper.testdata_dir])

        # Generate profile.
        profile = self.generate_profile(None, [testdata_file])

        CheckItem = namedtuple(
            'CheckItem', ['addr', 'source_file', 'source_line', 'func_name', 'func_start_line'])

        check_items = [
            CheckItem(0x113c, 'two_functions.cpp', 22, 'main', 20),
            CheckItem(0x1140, 'two_functions.cpp', 23, 'main', 20),
            CheckItem(0x1094, 'two_functions.cpp', 9, 'Function1', 6),
            CheckItem(0x1104, 'two_functions.cpp', 16, 'Function2', 13),
        ]
        mapping = None
        for mapping in profile.mapping:
            binary_path = profile.string_table[mapping.filename]
            if 'runtest_two_functions_arm64' in binary_path:
                self.assertTrue(mapping.has_line_numbers)
                mapping = mapping
                break
        self.assertIsNotNone(mapping)

        for check_item in check_items:
            found = False
            for location in profile.location:
                if location.mapping_id != mapping.id:
                    continue
                addr = location.address - mapping.memory_start + mapping.file_offset
                if addr == check_item.addr:
                    found = True
                    self.assertEqual(len(location.line), 1)
                    line = location.line[0]
                    function = profile.function[line.function_id - 1]
                    self.assertIn(check_item.source_file, profile.string_table[function.filename])
                    self.assertEqual(line.line, check_item.source_line)
                    self.assertIn(check_item.func_name, profile.string_table[function.name])
                    self.assertEqual(function.start_line, check_item.func_start_line)
                    break
            self.assertTrue(found, check_item)

    def test_function_name_not_changed_by_line_info(self):
        """ Adding line info shouldn't override function names from report library, which are more
            accurate when proguard mapping file is given.
        """
        testdata_file = TestHelper.testdata_path('runtest_two_functions_arm64_perf.data')

        # Build binary_cache.
        binary_cache_builder = BinaryCacheBuilder(TestHelper.ndk_path, False)
        binary_cache_builder.build_binary_cache(testdata_file, [TestHelper.testdata_dir])

        # Read recording file.
        config = {'ndk_path': None, 'max_chain_length': 1000000, 'proguard_mapping_file': None}
        generator = PprofProfileGenerator(config)
        generator.load_record_file(testdata_file)

        # Change function name.
        sample = generator.sample_list[0]
        self.assertGreaterEqual(len(sample.location_ids), 1)
        location = generator.location_list[sample.location_ids[0] - 1]
        self.assertGreaterEqual(len(location.lines), 1)
        function = generator.get_function(location.lines[0].function_id)
        function_name = generator.get_string(function.name_id)
        self.assertEqual(function_name, 'Function1()')
        location.lines[0].function_id = generator.get_function_id(
            'NewFunction1()', generator.get_string(function.dso_name_id), function.vaddr_in_dso)

        # Add line info.
        generator.gen_source_lines(1)

        # Check function name and line info.
        sample = generator.sample_list[0]
        self.assertGreaterEqual(len(sample.location_ids), 1)
        location = generator.location_list[sample.location_ids[0] - 1]
        self.assertGreaterEqual(len(location.lines), 1)
        function = generator.get_function(location.lines[0].function_id)
        function_name = generator.get_string(function.name_id)
        self.assertEqual(function_name, 'NewFunction1()')
        self.assertNotEqual(function.source_filename_id, 0)
        source_filename = generator.get_string(function.source_filename_id)
        self.assertIn('two_functions.cpp', source_filename)
