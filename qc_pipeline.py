import os
import errno
import glob
import argparse
import configparser
import shutil
import matplotlib as mpl  # need to do this before anything else tries to access
import multiprocessing, logging
from importlib import import_module
import warnings
import BaseImage

# --- setup logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    filename="error.log", filemode='w')

console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logging.getLogger('').addHandler(console)

# --- setup plotting backend
if os.name != "nt" and os.environ.get('DISPLAY', '') == '':
    logging.info('no display found. Using non-interactive Agg backend')
    mpl.use('Agg')
else:
    mpl.use('TkAgg')

import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")

# ---Setup globals for output
batch = 1
nfiledone = 0
csv_report = None
first = True
failed = []


# --- setup worker functions
def worker(filei, nfiles, fname, args, lconfig, processQueue):
    fname_outdir = args.outdir + os.sep + os.path.basename(fname)
    if os.path.isdir(fname_outdir):  # directory exists
        if (args.force):  # remove entirey directory to ensure no old files are present
            shutil.rmtree(fname_outdir)
        else:  # otherwise skip it
            logging.warning(fname,
                            " already seems to be processed (output directory exists), skipping. To avoid this behavior use "
                            "--force")
        return
    makeDir(fname_outdir)

    logging.info(f"-----Working on:\t{fname}\t\t{filei} of {nfiles}")
    s = BaseImage.BaseImage(fname, fname_outdir, dict(lconfig.items("BaseImage.BaseImage")))

    for process, process_params in processQueue:
        process(s, process_params)
        s["completed"].append(process.__name__)

    s["os_handle"] = None #need to get rid of handle because it can't be pickled
    return s


def worker_callback(s):
    if s is None:
        return

    global csv_report, batch, first, nfiledone

    if nfiledone and nfiledone % args.batch == 0:
        csv_report.close()
        batch += 1
        csv_report = open(args.outdir + os.sep + "results_" + str(batch) + ".tsv", overwrite_flag, buffering=1)
        first = True

    if first and overwrite_flag == "w":  # add headers to output file, don't do this if we're in append mode
        first = False
        for field in s["output"]:
            csv_report.write(field + "\t")
        csv_report.write("warnings")  # always add warnings field
        csv_report.write("\n")

    for field in s["output"]:
        csv_report.write(s[field] + "\t")

    csv_report.write("|".join(s["warnings"]) + "\n")
    csv_report.flush()


def worker_error(e):
    #     fname = e.args
    print("ERROR!")
    print(e)


#     err_string = " ".join((str(e.__class__), e.__doc__, str(e)))
#     logging.error("--->Error analyzing file (skipping):\t", fname)
#     logging.error("--->Error was ", err_string)
#     failed.append((fname, err_string))


def load_pipeline(lconfig):
    lprocessQueue = []
    logging.info("Pipeline will use these steps:")
    for process in lconfig.get('pipeline', 'steps').splitlines():
        mod_name, func_name = process.split('.')
        logging.info(f"\t\t{mod_name}\t{func_name}")
        try:
            mod = import_module(mod_name)
        except:
            raise NameError("Unknown module in pipeline from config file:\t %s" % mod_name)

        try:
            func_name = func_name.split(":")[0]  # take base of function name
            func = getattr(mod, func_name)
        except:
            raise NameError(
                "Unknown function from module in pipeline from config file: \t%s \t in \t %s" % (mod_name, func_name))

        if lconfig.has_section(process):
            params = dict(lconfig.items(process))
        else:
            params = {}

        lprocessQueue.append((func, params))
    return lprocessQueue


def makeDir(path):
    try:
        os.makedirs(path)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise


if __name__ == '__main__':

    manager = multiprocessing.Manager()
    lock = manager.Lock()

    parser = argparse.ArgumentParser(description='')
    parser.add_argument('input_pattern', help="input filename pattern (try: '*.svs')")
    parser.add_argument('-o', '--outdir', help="outputdir, default ./output/", default="output", type=str)
    parser.add_argument('-c', '--config', help="config file to use", default="./config.ini", type=str)
    parser.add_argument('-f', '--force', help="force overwriting of existing files", action="store_true")
    parser.add_argument('-b', '--batch', help="break results file into subfiles of this size", type=int,
                        default=float("inf"))
    parser.add_argument('-n', '--nthreads', help="number of threads to launch", type=int, default=2)
    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(args.config)

    processQueue = load_pipeline(config)
    if args.nthreads > 1:
        pool = multiprocessing.Pool(processes=args.nthreads, initializer=load_pipeline,
                                    initargs=(config,))  # start worker processes
    logging.info("----------")
    # make output directory and create report file
    makeDir(args.outdir)

    if len(glob.glob(args.outdir + os.sep + "results*.tsv")) > 0:
        if (args.force):
            logging.info("Previous run detected....overwriting (--force set)")
            overwrite_flag = "w"
        else:
            logging.info("Previous run detected....skipping completed (--force not set)")
            overwrite_flag = "a"
    else:
        overwrite_flag = "w"

    if (args.batch != float("inf")):
        csv_report = open(args.outdir + os.sep + "results_" + str(batch) + ".tsv", overwrite_flag, buffering=1)
    else:
        csv_report = open(args.outdir + os.sep + "results.tsv", overwrite_flag, buffering=1)

    files = glob.glob(args.input_pattern)
    for filei, fname in enumerate(files):
        res = pool.apply_async(worker, args=(filei, len(files), fname, args, config, processQueue),
                               callback=worker_callback, error_callback=worker_error)
        # res = pool.apply_async(worker, args=(filei, len(files), fname, args, config, processQueue))

    pool.close()
    pool.join()
    csv_report.close()
    shutil.move("error.log", args.outdir + os.sep + "error.log")  # move error log to output directory

    logging.info("------------Done---------\n")
    logging.info("These images failed (available also in error.log), warnings are listed in warnings column in output:")

    for fname, error in failed:  # TODO: FIX
        logging.info(fname, error, sep="\t")
