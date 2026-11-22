# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
cmake_minimum_required(VERSION 3.24)
project(PublicLibKML VERSION 1.3.0 LANGUAGES CXX)

foreach(required KML_SOURCE EXPAT_PREFIX BOOST_SOURCE MINIZIP_ARCHIVE MINIZIP_INCLUDE ZLIB_PREFIX URIPARSER_PREFIX)
  if(NOT ${required})
    message(FATAL_ERROR "Explicit verified ${required} required")
  endif()
endforeach()

set(KML_BASE_SOURCES
  attributes.cc
  csv_splitter.cc
  date_time.cc
  expat_handler_ns.cc
  expat_parser.cc
  file.cc
  file_posix.cc
  math_util.cc
  mimetypes.cc
  referent.cc
  string_util.cc
  time_util.cc
  uri_parser.cc
  version.cc
  xml_namespaces.cc
  zip_file.cc
)

set(KML_DOM_SOURCES
  abstractlatlonbox.cc
  abstractview.cc
  atom.cc
  balloonstyle.cc
  colorstyle.cc
  container.cc
  document.cc
  element.cc
  extendeddata.cc
  feature.cc
  folder.cc
  geometry.cc
  hotspot.cc
  iconstyle.cc
  kml_cast.cc
  kml_factory.cc
  kml.cc
  link.cc
  labelstyle.cc
  linestyle.cc
  liststyle.cc
  model.cc
  networklink.cc
  networklinkcontrol.cc
  object.cc
  overlay.cc
  placemark.cc
  polystyle.cc
  region.cc
  schema.cc
  snippet.cc
  style.cc
  stylemap.cc
  styleselector.cc
  substyle.cc
  timeprimitive.cc
  gx_timeprimitive.cc
  gx_tour.cc
  vec2.cc
  kml_handler.cc
  kml_handler_ns.cc
  parser.cc
  serializer.cc
  xal.cc
  xml_serializer.cc
  xsd.cc
  visitor.cc
  visitor_driver.cc
)

set(KML_ENGINE_SOURCES
  clone.cc
  entity_mapper.cc
  feature_balloon.cc
  feature_view.cc
  feature_visitor.cc
  find.cc
  find_xml_namespaces.cc
  get_link_parents.cc
  get_links.cc
  href.cc
  id_mapper.cc
  kml_cache.cc
  kml_file.cc
  kml_stream.cc
  kml_uri.cc
  kmz_cache.cc
  kmz_file.cc
  link_util.cc
  location_util.cc
  merge.cc
  parse_old_schema.cc
  style_inliner.cc
  style_merger.cc
  style_resolver.cc
  style_splitter.cc
  update_processor.cc
  update.cc
)

set(KML_CONVENIENCE_SOURCES
  atom_util.cc
  convenience.cc
  csv_file.cc
  csv_parser.cc
  feature_list.cc
  google_doc_list.cc
  google_maps_data.cc
  google_picasa_web.cc
  google_spreadsheets.cc
  http_client.cc
  kmz_check_links.cc
)

set(KML_REGIONATOR_SOURCES
  feature_list_region_handler.cc
  regionator.cc
  regionator_util.cc
)

set(KML_XSD_SOURCES
  xsd_complex_type.cc
  xsd_element.cc
  xsd_file.cc
  xsd_handler.cc
  xsd_primitive_type.cc
  xsd_util.cc
  xst_parser.cc
)

function(add_kml_family family files_var)
  set(sources)
  foreach(name IN LISTS ${files_var})
    list(APPEND sources "${KML_SOURCE}/src/kml/${family}/${name}")
  endforeach()
  add_library(kml${family} STATIC ${sources})
  target_compile_features(kml${family} PUBLIC cxx_std_17)
  target_include_directories(kml${family} PUBLIC
    "$<BUILD_INTERFACE:${KML_SOURCE}/src>"
    "$<INSTALL_INTERFACE:include>"
    "${BOOST_SOURCE}"
  )
  target_include_directories(kml${family} PRIVATE
    "${EXPAT_PREFIX}/include"
    "${URIPARSER_PREFIX}/include"
    "${MINIZIP_INCLUDE}"
    "${ZLIB_PREFIX}/include"
  )
  set_target_properties(kml${family} PROPERTIES POSITION_INDEPENDENT_CODE ON)
  install(TARGETS kml${family} ARCHIVE DESTINATION lib)
endfunction()

add_kml_family(base KML_BASE_SOURCES)
add_kml_family(dom KML_DOM_SOURCES)
add_kml_family(engine KML_ENGINE_SOURCES)
add_kml_family(convenience KML_CONVENIENCE_SOURCES)
add_kml_family(regionator KML_REGIONATOR_SOURCES)
add_kml_family(xsd KML_XSD_SOURCES)

target_link_libraries(kmlbase PUBLIC
  "${EXPAT_PREFIX}/lib/libexpat.a"
  "${MINIZIP_ARCHIVE}"
  "${ZLIB_PREFIX}/lib/libz.a"
  "${URIPARSER_PREFIX}/lib/liburiparser.a"
)
target_link_libraries(kmldom PUBLIC kmlbase)
target_link_libraries(kmlengine PUBLIC kmldom kmlbase)
target_link_libraries(kmlconvenience PUBLIC kmlengine kmldom kmlbase)
target_link_libraries(kmlregionator PUBLIC kmlconvenience kmlengine kmldom kmlbase)
target_link_libraries(kmlxsd PUBLIC kmlbase)

install(DIRECTORY "${KML_SOURCE}/src/kml" DESTINATION include FILES_MATCHING PATTERN "*.h")
