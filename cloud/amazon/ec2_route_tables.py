#!/usr/bin/python
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

DOCUMENTATION = '''
---
module: ec2_route_tables
short_description: Configures AWS VPC route tables.
description:
    - Create or removes AWS VPC route tables.  This module has a dependency on python-boto.
version_added: "1.8"
options:
  vpc_id:
    description:
      - A VPC id to terminate when state=absent
    required: false
    default: null
    aliases: []
  route_tables:
    description:
      - 'A dictionary array of route tables to add of the form: { subnets: [172.22.2.0/24, 172.22.3.0/24,], routes: [{ dest: 0.0.0.0/0, gw: igw},] }. Where the subnets list is those subnets the route table should be associated with, and the routes list is a list of routes to be in the table.  The special keyword for the gw of igw specifies that you should the route should go through the internet gateway attached to the VPC. gw also accepts instance-ids in addition igw. This module is currently unable to affect the "main" route table due to some limitations in boto, so you must explicitly define the associated subnets or they will be attached to the main table implicitly.'
    required: false
    default: null
    aliases: []
  state:
    description:
      - Create or terminate the VPC
    required: true
    default: present
    aliases: []
  aws_secret_key:
    description:
      - AWS secret key. If not set then the value of the AWS_SECRET_KEY environment variable is used. 
    required: false
    default: None
    aliases: ['ec2_secret_key', 'secret_key' ]
  aws_access_key:
    description:
      - AWS access key. If not set then the value of the AWS_ACCESS_KEY environment variable is used.
    required: false
    default: None
    aliases: ['ec2_access_key', 'access_key' ]
  validate_certs:
    description:
      - When set to "no", SSL certificates will not be validated for boto versions >= 2.6.0.
    required: false
    default: "yes"
    choices: ["yes", "no"]
    aliases: []
    version_added: "1.5"

requirements: [ "boto" ]
author: erewh0n [keith.hassen (at) gmail.com]
'''

EXAMPLES = '''
TODO
'''


import sys
import time

try:
    import boto.ec2
    import boto.vpc
    from boto.exception import EC2ResponseError
except ImportError:
    print "failed=True msg='boto required for this module'"
    sys.exit(1)


def format_response(route_tables):
    return [{
            'id': table.id,
            'routes': [{
                    'cidr': route.destination_cidr_block,
                    'gateway_id': route.gateway_id,
                    'instance_id': route.instance_id,
                } for route in table.routes],
            'associations': [{
                    'subnet_id': association.subnet_id,
                    'is_main': association.main,
                } for association in table.associations],
            }
        for table in route_tables]

def delete_tables(vpc_conn, tables):
    for table in tables:
        is_main = False
        for association in table.associations:
            if not association.main:
                vpc_conn.disassociate_route_table(association.id)
            else:
                is_main = True
        if not is_main:
            vpc_conn.delete_route_table(table.id)


def create_tables(vpc_conn, vpc, gateway_id, tables, subnet_mapper):
    new_tables = []
    for table in tables:
        new_table = vpc_conn.create_route_table(vpc.id)

        for route in table['routes']:
            gateway = route['gw']
            if gateway == 'igw':
                vpc_conn.create_route(new_table.id, route['dest'], gateway_id)
            else:
                vpc_conn.create_route(new_table.id, route['dest'], None, route['gw'])

        for subnet_cidr in table['subnets']:
            vpc_conn.associate_route_table(new_table.id, subnet_mapper[subnet_cidr].id)

        new_tables.append(new_table)
    return new_tables


def find_matching_route(existing_routes, requested_route, subnet_mapper, gateway):
    for route in existing_routes:

        if route.gateway_id and (requested_route['gw'] != 'igw' or route.gateway_id != gateway.id):
            continue
        elif route.instance_id and route.instance_id != requested_route['gw']:
            continue

        if route.destination_cidr_block and route.destination_cidr_block != requested_route['dest']:
            continue

        return route
    return None

def find_matching_association(existing_associations, requested_subnet, subnet_mapper):
    for association in existing_associations:
        if requested_subnet == subnet_mapper[association.subnet_id].cidr_block:
            return association
    return None


def find_matching_table(existing_tables, requested_table, subnet_mapper, gateway):
    for table in existing_tables:
        matched = True
        routes = [route for route in table.routes if route.gateway_id != 'local']
        if len(requested_table['routes']) != len(routes):
            continue

        for route in requested_table['routes']:
            if not find_matching_route(routes, route, subnet_mapper, gateway):
                matched = False
                break
        if not matched:
            continue

        if len(requested_table['subnets']) != len(table.associations):
            continue

        for subnet in requested_table['subnets']:
            if not find_matching_association(table.associations, subnet, subnet_mapper):
                matched = False
                break
        if not matched:
            continue

        return table
    return None


def main():
    argument_spec = ec2_argument_spec()
    argument_spec.update(dict(
            vpc_id = dict(),
            vpc_name = dict(),
            route_tables = dict(type='list'),
            state = dict(choices=['present', 'absent'], default='present'),
        )
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
    )

    route_tables = module.params.get('route_tables')
    if route_tables and not isinstance(route_tables, list):
        module.fail_json(msg='route tables need to be a list of dictionaries')

    state = module.params.get('state')
    vpc_name = module.params.get('vpc_name')
    ec2_url, aws_access_key, aws_secret_key, region = get_ec2_creds(module)
   
    if region:
        try:
            vpc_conn = boto.vpc.connect_to_region(
                region,
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key
            )
        except boto.exception.NoAuthHandlerFound, e:
            module.fail_json(msg = str(e))
    else:
        module.fail_json(msg="VPC region must be specified.")

    vpc_name = module.params.get('vpc_name')
    vpc_id = module.params.get('vpc_id')
    if vpc_name and vpc_id:
        module.fail_json(msg = "Cannot specify both vpc_name and vpc_id.")
    if vpc_name:
        vpcs = vpc_conn.get_all_vpcs(filters={'tag:Name':vpc_name})
    else:
        vpcs = vpc_conn.get_all_vpcs([vpc_id])
    if vpcs is None or len(vpcs) != 1:
        module.fail_json(msg = "Could not find VPC {0}.".format(vpc_id))
    if vpc_name:
        vpc_id = vpcs[0].id

    gateways = vpc_conn.get_all_internet_gateways(filters={'attachment.vpc-id': vpcs[0].id})
    if len(gateways) > 1 :
        module.fail_json(msg='EC2 returned more than one Internet Gateway for ID %s.' % vpc.id)

    requested_tables = module.params.get('route_tables')
    # Get all tables in the VPC except for the main routing table.
    existing_tables = [
        table for table in vpc_conn.get_all_route_tables(filters = { 'vpc_id': vpcs[0].id })
        if len([association for association in table.associations if association.main]) == 0
    ]
    # Map from either ID or CIDR to subnet.
    # TODO KPH: make this simpler. :)
    subnet_mapper = dict((subnet.id, subnet)
        for subnet in vpc_conn.get_all_subnets(filters={'vpc_id': vpc_id}))
    subnet_mapper = dict(subnet_mapper.items() + dict((subnet.cidr_block, subnet)
        for subnet in vpc_conn.get_all_subnets(filters={'vpc_id': vpc_id})).items())

    new_tables = []
    matched_tables = []
    old_tables = list(existing_tables)
    for requested_table in requested_tables:
        matching_table = find_matching_table(existing_tables, requested_table, subnet_mapper, gateways[0])
        if matching_table:
            matched_tables.append(matching_table)
            old_tables.remove(matching_table)
        else:
            new_tables.append(requested_table)

    changed = len(old_tables) > 0 or len(new_tables) > 0

    # Delete all existing, unmatched tables.
    delete_tables(vpc_conn, old_tables)
    create_tables(vpc_conn, vpcs[0], gateways[0].id, new_tables, subnet_mapper)

    existing_tables = vpc_conn.get_all_route_tables(filters = { 'vpc_id': vpcs[0].id })

    module.exit_json(changed = changed, route_tables = format_response(existing_tables))


# import module snippets
from ansible.module_utils.basic import *
from ansible.module_utils.ec2 import *

main()
